// Package main parses an IEX HIST TOPS 1.6 file (pcapng, optionally gzipped)
// as a stream and reduces it to one daily bar per symbol. The file never has
// to be stored: a full day is ~10 GB compressed / ~50 GB uncompressed.
//
// Spec: IEX TOPS v1.6 and IEX Transport Protocol (IEX-TP) v1.
package main

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"strings"
)

const (
	blockSHB = 0x0A0D0D0A
	blockIDB = 0x00000001
	blockEPB = 0x00000006
	blockSPB = 0x00000003

	iexTPHeaderLen = 40

	msgSecurityDirectory = 'D'
	msgTradeReport       = 'T'
	msgTradeBreak        = 'B'
	msgOfficialPrice     = 'X'

	// Trade report sale-condition flags.
	flagExtendedHours = 0x40
	flagOddLot        = 0x20

	// Security directory flags.
	flagTestSecurity = 0x80
	flagWhenIssued   = 0x40
	flagETP          = 0x20
)

// Bar is one symbol's day built from IEX trades only.
type Bar struct {
	Symbol      string
	Open        float64 // first regular-hours round-lot trade
	High        float64
	Low         float64
	Close       float64 // last regular-hours round-lot trade
	Volume      int64   // all regular-hours shares, odd lots included
	Trades      int64
	OddLotOnly  bool    // no round-lot trade: OHLC come from odd lots
	OfficialCls float64 // IEX official closing price (IEX-listed only), 0 if none
	Test        bool
	ETP         bool
	WhenIssued  bool
	LastTS      int64 // ns since epoch, last trade counted

	hasRound bool
	oddOpen  float64
	oddHigh  float64
	oddLow   float64
	oddClose float64
	hasOdd   bool
}

// Stats summarise a run for logs and tests.
type Stats struct {
	Blocks   int64
	Packets  int64
	Messages int64
	Trades   int64
	Breaks   int64
	Bytes    int64
}

// Parser accumulates bars across the stream.
type Parser struct {
	Bars  map[string]*Bar
	Stats Stats
	buf   []byte
}

func NewParser() *Parser {
	return &Parser{Bars: make(map[string]*Bar, 12000), buf: make([]byte, 1<<16)}
}

func (p *Parser) bar(sym string) *Bar {
	b := p.Bars[sym]
	if b == nil {
		b = &Bar{Symbol: sym, Low: math.Inf(1), oddLow: math.Inf(1)}
		p.Bars[sym] = b
	}
	return b
}

// Parse reads pcapng blocks until EOF.
func (p *Parser) Parse(r io.Reader) error {
	br := bufio.NewReaderSize(r, 4<<20)
	hdr := make([]byte, 8)
	for {
		if _, err := io.ReadFull(br, hdr); err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			if errors.Is(err, io.ErrUnexpectedEOF) {
				return nil // truncated tail (e.g. a partial download): keep what we have
			}
			return err
		}
		btype := binary.LittleEndian.Uint32(hdr[0:4])
		blen := int(binary.LittleEndian.Uint32(hdr[4:8]))
		if blen < 12 || blen > 1<<24 {
			return fmt.Errorf("bad pcapng block length %d (type %#x) after %d blocks", blen, btype, p.Stats.Blocks)
		}
		body := blen - 8
		if cap(p.buf) < body {
			p.buf = make([]byte, body)
		}
		b := p.buf[:body]
		if _, err := io.ReadFull(br, b); err != nil {
			if errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		p.Stats.Blocks++
		p.Stats.Bytes += int64(blen)
		switch btype {
		case blockEPB:
			// interface(4) tsHigh(4) tsLow(4) capLen(4) origLen(4) data...
			if len(b) < 20 {
				continue
			}
			capLen := int(binary.LittleEndian.Uint32(b[12:16]))
			if 20+capLen > len(b) {
				continue
			}
			p.packet(b[20 : 20+capLen])
		case blockSPB:
			if len(b) < 4 {
				continue
			}
			p.packet(b[4 : len(b)-4])
		case blockSHB:
			// Merged files contain several section headers; nothing to do.
		}
	}
}

// packet strips Ethernet/IPv4/UDP and hands the IEX-TP segment on.
func (p *Parser) packet(d []byte) {
	if len(d) < 14 {
		return
	}
	off := 12
	etype := binary.BigEndian.Uint16(d[off:])
	off += 2
	for etype == 0x8100 && len(d) >= off+4 { // VLAN tag(s)
		etype = binary.BigEndian.Uint16(d[off+2:])
		off += 4
	}
	if etype != 0x0800 || len(d) < off+20 {
		return
	}
	ihl := int(d[off]&0x0f) * 4
	if d[off+9] != 17 || len(d) < off+ihl+8 {
		return
	}
	off += ihl + 8
	p.Stats.Packets++
	p.segment(d[off:])
}

// segment walks the messages of one IEX-TP segment.
func (p *Parser) segment(s []byte) {
	if len(s) < iexTPHeaderLen {
		return
	}
	payloadLen := int(binary.LittleEndian.Uint16(s[12:14]))
	count := int(binary.LittleEndian.Uint16(s[14:16]))
	end := iexTPHeaderLen + payloadLen
	if end > len(s) {
		end = len(s)
	}
	off := iexTPHeaderLen
	for i := 0; i < count && off+2 <= end; i++ {
		ml := int(binary.LittleEndian.Uint16(s[off:]))
		off += 2
		if off+ml > end || ml == 0 {
			return
		}
		p.message(s[off : off+ml])
		off += ml
	}
}

func symbol(b []byte) string {
	return strings.TrimRight(string(b), " \x00")
}

func price(b []byte) float64 {
	return float64(int64(binary.LittleEndian.Uint64(b))) / 10000
}

func (p *Parser) message(m []byte) {
	p.Stats.Messages++
	switch m[0] {
	case msgTradeReport:
		if len(m) < 38 {
			return
		}
		p.Stats.Trades++
		flags := m[1]
		if flags&flagExtendedHours != 0 {
			return
		}
		ts := int64(binary.LittleEndian.Uint64(m[2:10]))
		b := p.bar(symbol(m[10:18]))
		size := int64(binary.LittleEndian.Uint32(m[18:22]))
		px := price(m[22:30])
		b.Volume += size
		b.Trades++
		b.LastTS = ts
		if flags&flagOddLot != 0 {
			if !b.hasOdd {
				b.oddOpen = px
				b.hasOdd = true
			}
			b.oddClose = px
			b.oddHigh = math.Max(b.oddHigh, px)
			b.oddLow = math.Min(b.oddLow, px)
			return
		}
		if !b.hasRound {
			b.Open = px
			b.hasRound = true
		}
		b.Close = px
		b.High = math.Max(b.High, px)
		b.Low = math.Min(b.Low, px)
	case msgTradeBreak:
		p.Stats.Breaks++ // rare; the bar is not rebuilt (documented limitation)
	case msgOfficialPrice:
		if len(m) < 26 || m[1] != 'M' {
			return
		}
		p.bar(symbol(m[10:18])).OfficialCls = price(m[18:26])
	case msgSecurityDirectory:
		if len(m) < 31 {
			return
		}
		b := p.bar(symbol(m[10:18]))
		b.Test = m[1]&flagTestSecurity != 0
		b.WhenIssued = m[1]&flagWhenIssued != 0
		b.ETP = m[1]&flagETP != 0
	}
}

// Finish resolves odd-lot-only bars and drops symbols that never traded.
func (p *Parser) Finish() []*Bar {
	out := make([]*Bar, 0, len(p.Bars))
	for _, b := range p.Bars {
		if !b.hasRound && b.hasOdd {
			b.Open, b.High, b.Low, b.Close = b.oddOpen, b.oddHigh, b.oddLow, b.oddClose
			b.OddLotOnly = true
		}
		if !b.hasRound && !b.hasOdd {
			b.Low = 0
		}
		out = append(out, b)
	}
	return out
}
