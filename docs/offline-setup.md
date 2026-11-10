# Offline setup and verification

## What "offline" means in Bhap

- After setup, `add`, `search`, `ask`, `chat`, `summarize`, `compare`, `study`, `notes`, `history`, `backup`, `graph` and `watch` never need the internet.
- At startup the `bhap` process installs a **network guard**. Any DNS lookup or socket connection to a non-loopback address raises an error instead of leaving the machine. The parse worker processes run under the same guard.
- The model runtime URL must be loopback (`127.0.0.1`, `localhost`, `::1`). Remote runtimes are refused.
- HTTP proxies from the environment are ignored for runtime traffic.
- Downloads (`bhap setup --download`, `bhap model pull`) are separate, explicit commands. They ask the *local* Ollama daemon to fetch a model; the `bhap` process itself still only talks to loopback.

The guard protects Bhap's own process. It does not firewall other programs, including the Ollama daemon. To verify the whole stack, block egress at the OS level too.

## Checklist

1. While online: install Bhap with the extras you need, install the runtime, run `bhap setup`, and optionally install OCR.
2. Run `bhap doctor --offline`. Every required item must be ✓.
3. Disconnect for real: turn off Wi-Fi *and* unplug Ethernet, or block egress (see below).
4. Run `bhap doctor --offline --verify`. This makes a real embedding and chat call.
5. Run the core flow: `bhap add`, `bhap search`, `bhap ask`, `bhap`, then restart the terminal and check that your workspace, documents and history are still there.

## Blocking egress for a strict test (Linux)

```sh
# nftables: allow loopback only, for this session
sudo nft add table inet bhaptest
sudo nft add chain inet bhaptest out '{ type filter hook output priority 0; policy drop; }'
sudo nft add rule inet bhaptest out oif lo accept
# … run the checklist …
sudo nft delete table inet bhaptest
```

## Readiness states

`bhap status` and the session header show one of:

| State | Meaning | Fix |
|---|---|---|
| ● Offline ready | runtime up, both models installed | — |
| ● Setup incomplete | runtime up, a model is missing or not chosen | `bhap setup` |
| ● Model unavailable | runtime not reachable | `ollama serve` / `systemctl start ollama` |

Documents are never reported as searchable until they are fully indexed. A crash or Ctrl+C during import leaves no partial document. The job is recorded as `interrupted`/`cancelled` and `bhap jobs retry` resumes it.
