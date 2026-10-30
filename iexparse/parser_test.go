package main

import (
	"bytes"
	"encoding/binary"
	"testing"
)

// --- builders for a synthetic pcapng stream ---

func le16(v uint16) []byte { b := make([]byte, 2); binary.LittleEndian.PutUint16(b, v); return b }
func le32(v uint32) []byte { b := make([]byte, 4); binary.LittleEndian.PutUint32(b, v); return b }
func le64(v uint64) []byte { b := make([]byte, 8); binary.LittleEndian.PutUint64(b, v); return b }

func sym8(s string) []byte {
	b := []byte("        ")
	copy(b, s)
	return b
}

func trade(flags byte, ts int64, sym string, size uint32, px float64) []byte {
	m := []byte{msgTradeReport, flags}
	m = append(m, le64(uint64(ts))...)
	m = append(m, sym8(sym)...)
	m = append(m, le32(size)...)
	m = append(m, le64(uint64(int64(px*10000+0.5)))...)
	m = append(m, le64(1)...)
	return m
}

func official(sym string, px float64) []byte {
	m := []byte{msgOfficialPrice, 'M'}
	m = append(m, le64(1)...)
	m = append(m, sym8(sym)...)
	return append(m, le64(uint64(int64(px*10000+0.5)))...)
}

func directory(flags byte, sym string) []byte {
	m := []byte{msgSecurityDirectory, flags}
	m = append(m, le64(1)...)
	m = append(m, sym8(sym)...)
	m = append(m, le32(100)...)
	m = append(m, le64(0)...)
	return append(m, 1)
}

func segment(msgs ...[]byte) []byte {
	var payload []byte
	for _, m := range msgs {
		payload = append(payload, le16(uint16(len(m)))...)
		payload = append(payload, m...)
	}
	h := make([]byte, iexTPHeaderLen)
	h[0] = 1
	binary.LittleEndian.PutUint16(h[2:], 0x8003)
	binary.LittleEndian.PutUint16(h[12:], uint16(len(payload)))
	binary.LittleEndian.PutUint16(h[14:], uint16(len(msgs)))
	return append(h, payload...)
}

func udpFrame(seg []byte) []byte {
	eth := make([]byte, 14)
	binary.BigEndian.PutUint16(eth[12:], 0x0800)
	ip := make([]byte, 20)
	ip[0] = 0x45
	ip[9] = 17
	udp := make([]byte, 8)
	f := append(eth, ip...)
	f = append(f, udp...)
	return append(f, seg...)
}

func block(btype uint32, body []byte) []byte {
	for len(body)%4 != 0 {
		body = append(body, 0)
	}
	total := uint32(len(body) + 12)
	b := append(le32(btype), le32(total)...)
	b = append(b, body...)
	return append(b, le32(total)...)
}

func epb(frame []byte) []byte {
	body := append(le32(0), le32(0)...)
	body = append(body, le32(0)...)
	body = append(body, le32(uint32(len(frame)))...)
	body = append(body, le32(uint32(len(frame)))...)
	return block(blockEPB, append(body, frame...))
}

func shb() []byte {
	body := append(le32(0x1A2B3C4D), le16(1)...)
	body = append(body, le16(0)...)
	body = append(body, le64(^uint64(0))...)
	return block(blockSHB, body)
}

func stream(segs ...[]byte) []byte {
	var buf bytes.Buffer
	buf.Write(shb())
	buf.Write(block(blockIDB, append(le16(1), make([]byte, 6)...)))
	for _, s := range segs {
		buf.Write(epb(udpFrame(s)))
	}
	return buf.Bytes()
}

// --- tests ---

func parse(t *testing.T, data []byte) map[string]*Bar {
	t.Helper()
	p := NewParser()
	if err := p.Parse(bytes.NewReader(data)); err != nil {
		t.Fatal(err)
	}
	out := map[string]*Bar{}
	for _, b := range p.Finish() {
		out[b.Symbol] = b
	}
	return out
}

func TestRoundLotOHLCAndVolume(t *testing.T) {
	data := stream(
		segment(trade(0, 1, "ACME", 100, 10.00), trade(0, 2, "ACME", 200, 12.50)),
		segment(trade(flagOddLot, 3, "ACME", 7, 99.00), trade(0, 4, "ACME", 100, 9.50), trade(0, 5, "ACME", 300, 11.00)),
	)
	b := parse(t, data)["ACME"]
	if b == nil {
		t.Fatal("ACME missing")
	}
	if b.Open != 10 || b.High != 12.5 || b.Low != 9.5 || b.Close != 11 {
		t.Fatalf("OHLC = %v %v %v %v", b.Open, b.High, b.Low, b.Close)
	}
	if b.Volume != 707 || b.Trades != 5 {
		t.Fatalf("volume %d trades %d", b.Volume, b.Trades)
	}
	if b.OddLotOnly {
		t.Fatal("should not be odd-lot only")
	}
}

func TestExtendedHoursIgnored(t *testing.T) {
	data := stream(segment(trade(flagExtendedHours, 1, "PRE", 100, 50), trade(0, 2, "PRE", 100, 51)))
	b := parse(t, data)["PRE"]
	if b.Open != 51 || b.Close != 51 || b.Volume != 100 {
		t.Fatalf("got open %v close %v vol %d", b.Open, b.Close, b.Volume)
	}
}

func TestOddLotOnlyFallback(t *testing.T) {
	data := stream(segment(trade(flagOddLot, 1, "THIN", 5, 3.10), trade(flagOddLot, 2, "THIN", 5, 3.30)))
	b := parse(t, data)["THIN"]
	if !b.OddLotOnly || b.Open != 3.1 || b.Close != 3.3 || b.High != 3.3 || b.Low != 3.1 {
		t.Fatalf("got %+v", b)
	}
}

func TestDirectoryAndOfficialClose(t *testing.T) {
	data := stream(segment(directory(flagTestSecurity, "ZIEXT"), directory(flagETP, "SPY"), trade(0, 1, "SPY", 100, 500), official("SPY", 501.25)))
	bars := parse(t, data)
	if !bars["ZIEXT"].Test {
		t.Fatal("ZIEXT should be a test security")
	}
	if !bars["SPY"].ETP || bars["SPY"].OfficialCls != 501.25 {
		t.Fatalf("SPY %+v", bars["SPY"])
	}
}

func TestTruncatedStreamKeepsData(t *testing.T) {
	data := stream(segment(trade(0, 1, "CUT", 100, 1)), segment(trade(0, 2, "CUT", 100, 2)))
	b := parse(t, data[:len(data)-10])["CUT"]
	if b == nil || b.Close != 1 {
		t.Fatalf("expected first trade kept, got %+v", b)
	}
}
