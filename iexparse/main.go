package main

import (
	"bufio"
	"compress/gzip"
	"encoding/csv"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"time"
)

// Usage:
//
//	iexparse -date 2026-10-27 < TOPS.pcap.gz > bars.csv
//	curl -s URL | iexparse -date 2026-10-27 -o bars.csv
func main() {
	date := flag.String("date", "", "session date (YYYY-MM-DD), written to every row")
	out := flag.String("o", "-", "output CSV path, - for stdout")
	plain := flag.Bool("plain", false, "input is not gzipped")
	flag.Parse()
	if *date == "" {
		fmt.Fprintln(os.Stderr, "iexparse: -date is required")
		os.Exit(2)
	}

	var in io.Reader = bufio.NewReaderSize(os.Stdin, 8<<20)
	if !*plain {
		gz, err := gzip.NewReader(in)
		if err != nil {
			fmt.Fprintln(os.Stderr, "iexparse: gzip:", err)
			os.Exit(1)
		}
		gz.Multistream(true)
		in = gz
	}

	start := time.Now()
	p := NewParser()
	if err := p.Parse(in); err != nil {
		fmt.Fprintln(os.Stderr, "iexparse:", err)
		os.Exit(1)
	}
	bars := p.Finish()
	sort.Slice(bars, func(i, j int) bool { return bars[i].Symbol < bars[j].Symbol })

	var w io.Writer = os.Stdout
	if *out != "-" {
		f, err := os.Create(*out)
		if err != nil {
			fmt.Fprintln(os.Stderr, "iexparse:", err)
			os.Exit(1)
		}
		defer f.Close()
		w = f
	}
	if err := writeCSV(w, *date, bars); err != nil {
		fmt.Fprintln(os.Stderr, "iexparse:", err)
		os.Exit(1)
	}
	s := p.Stats
	fmt.Fprintf(os.Stderr, "iexparse: %d symbols, %d trades, %d messages, %d packets, %.1f MB pcap in %s\n",
		len(bars), s.Trades, s.Messages, s.Packets, float64(s.Bytes)/1e6, time.Since(start).Round(time.Millisecond))
}

func f(v float64) string { return strconv.FormatFloat(v, 'f', 4, 64) }

func b2s(b bool) string {
	if b {
		return "1"
	}
	return "0"
}

func writeCSV(w io.Writer, date string, bars []*Bar) error {
	cw := csv.NewWriter(w)
	_ = cw.Write([]string{"date", "symbol", "open", "high", "low", "close", "volume", "trades",
		"odd_lot_only", "official_close", "test", "etp", "when_issued", "last_ts"})
	for _, b := range bars {
		if err := cw.Write([]string{date, b.Symbol, f(b.Open), f(b.High), f(b.Low), f(b.Close),
			strconv.FormatInt(b.Volume, 10), strconv.FormatInt(b.Trades, 10), b2s(b.OddLotOnly),
			f(b.OfficialCls), b2s(b.Test), b2s(b.ETP), b2s(b.WhenIssued), strconv.FormatInt(b.LastTS, 10)}); err != nil {
			return err
		}
	}
	cw.Flush()
	return cw.Error()
}
