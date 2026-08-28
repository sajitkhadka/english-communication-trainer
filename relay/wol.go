package main

import (
	"fmt"
	"log"
	"net"
	"sync"
	"time"
)

// Waker sends Wake-on-LAN magic packets to the PC.
//
// ADR 0006: "queued work plus a stale heartbeat triggers a magic packet." Both halves
// matter. Waking on a stale heartbeat alone would keep a deliberately-sleeping machine
// awake all night for nothing; waking on queued work alone would fire while the PC is
// up and merely slow to drain.
//
// This is best-effort by nature - the packet is UDP, and whether it wakes anything
// depends on BIOS and NIC settings on the PC. Nothing downstream depends on it
// succeeding: the capture is already safe in the inbox, and it drains whenever the PC
// next comes up, wake packet or not.
type Waker struct {
	mac       net.HardwareAddr
	broadcast string
	cooldown  time.Duration

	mu       sync.Mutex
	lastSent time.Time
}

func NewWaker(mac, broadcast string, cooldown time.Duration) (*Waker, error) {
	if mac == "" {
		return nil, nil // waking disabled; a valid configuration
	}
	hw, err := net.ParseMAC(mac)
	if err != nil {
		return nil, fmt.Errorf("ECT_RELAY_WOL_MAC: %w", err)
	}
	if len(hw) != 6 {
		return nil, fmt.Errorf("ECT_RELAY_WOL_MAC: need a 6-byte MAC, got %d bytes", len(hw))
	}
	return &Waker{mac: hw, broadcast: broadcast, cooldown: cooldown}, nil
}

// magicPacket is the standard payload: six 0xFF bytes, then the MAC sixteen times.
func (w *Waker) magicPacket() []byte {
	packet := make([]byte, 0, 6+16*6)
	for range 6 {
		packet = append(packet, 0xFF)
	}
	for range 16 {
		packet = append(packet, w.mac...)
	}
	return packet
}

// Wake sends one packet, honouring the cooldown. Reports whether it actually sent.
func (w *Waker) Wake() (bool, error) {
	if w == nil {
		return false, nil
	}
	w.mu.Lock()
	if time.Since(w.lastSent) < w.cooldown {
		w.mu.Unlock()
		return false, nil
	}
	w.lastSent = time.Now()
	w.mu.Unlock()

	conn, err := net.Dial("udp", w.broadcast)
	if err != nil {
		return false, fmt.Errorf("dial %s: %w", w.broadcast, err)
	}
	defer conn.Close()
	if _, err := conn.Write(w.magicPacket()); err != nil {
		return false, fmt.Errorf("send magic packet: %w", err)
	}
	return true, nil
}

// WatchInbox wakes the PC while captures are waiting and the heartbeat is stale.
func WatchInbox(inbox *Inbox, presence *Presence, waker *Waker, every time.Duration, stop <-chan struct{}) {
	if waker == nil {
		log.Print("wake-on-LAN disabled (ECT_RELAY_WOL_MAC unset)")
		return
	}
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			if presence.Online() {
				continue
			}
			n, err := inbox.PendingCount()
			if err != nil {
				log.Printf("wol: could not count pending captures: %v", err)
				continue
			}
			if n == 0 {
				continue
			}
			sent, err := waker.Wake()
			if err != nil {
				log.Printf("wol: %v", err)
				continue
			}
			if sent {
				log.Printf("wol: magic packet sent (%d capture(s) waiting)", n)
			}
		}
	}
}
