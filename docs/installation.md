# Installation

`git clone` gives you the code only. Python, the model runtime, models and the optional OCR tools are prerequisites you install yourself. Bhap never installs or downloads anything silently.

## 1. Python package

```sh
git clone https://github.com/<owner>/bhap.git
cd bhap
python -m venv .venv
source .venv/bin/activate          # fish: source .venv/bin/activate.fish · PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[all]"  # minimal: python -m pip install -e .
bhap --version
```

| Extra | Adds | Why |
|---|---|---|
| (none) | `pypdf` | Everything works |
| `[fast]` | `numpy` | Faster vector search on large collections |
| `[crypto]` | `cryptography` | `bhap backup create --encrypt` |
| `[all]` | both | Recommended |

**Run `bhap` from any folder (recommended):** install it as an isolated tool, so it never touches the system Python. This works on Arch/CachyOS without `--break-system-packages`:

```sh
uv tool install --force --editable "/path/to/bhap[all]"   # puts `bhap` in ~/.local/bin
```

Alternatives once a release is published on PyPI: `pipx install "bhap-ai[all]"` or `uv tool install "bhap-ai[all]"`.

## 2. Local model runtime

**Ollama (recommended)**

- Arch / CachyOS: `sudo pacman -S ollama` (NVIDIA: `ollama-cuda`, AMD: `ollama-rocm`), then `systemctl enable --now ollama`
- Ubuntu / other Linux: installer from <https://ollama.com/download>
- macOS / Windows: the desktop installer from the same page

**Without sudo (user-local Ollama):** download the official release into your home directory:

```sh
mkdir -p ~/.local/opt/ollama ~/.local/bin && cd ~/.local/opt/ollama
curl -fL -o ollama.tar.zst https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst
tar --zstd -xf ollama.tar.zst && rm ollama.tar.zst
ln -sf ~/.local/opt/ollama/bin/ollama ~/.local/bin/ollama
OLLAMA_HOST=127.0.0.1:11434 ollama serve        # or start it automatically, see below
```

To start it automatically at login (optional), create `~/.config/systemd/user/ollama.service`:

```ini
[Unit]
Description=Ollama (user, loopback only)

[Service]
ExecStart=%h/.local/bin/ollama serve
Environment=OLLAMA_HOST=127.0.0.1:11434
Restart=on-failure

[Install]
WantedBy=default.target
```

then run `systemctl --user enable --now ollama`.

Then run `bhap setup`. It inspects RAM/GPU, suggests models for your hardware profile and asks before downloading. Nothing is fetched without your confirmation.

**llama.cpp**: start `llama-server -m model.gguf --port 8080` (add `--embeddings` and run a second server, or use Ollama, for embeddings), then `bhap config set runtime llamacpp`. Bhap accepts only loopback URLs.

## 3. Optional: OCR for scanned PDFs

| OS | Command |
|---|---|
| Arch / CachyOS | `sudo pacman -S tesseract tesseract-data-ind tesseract-data-eng poppler` |
| Ubuntu | `sudo apt install tesseract-ocr tesseract-ocr-ind poppler-utils` |
| macOS | `brew install tesseract tesseract-lang poppler` |

`bhap doctor` shows whether OCR is available and which languages it can use (`bhap config set ocr_languages ind+eng`).

## 4. Air-gapped installation

On an online machine with the **same OS, CPU architecture and Python version**:

```sh
./scripts/offline-bundle.sh            # writes bhap-offline-bundle/ with wheels for Bhap and its extras
ollama pull qwen2.5:7b && ollama pull bge-m3
```

Copy `bhap-offline-bundle/`, the Ollama installer and the Ollama model directory (`~/.ollama/models`, or `/usr/share/ollama/.ollama/models` for the system service) to the offline machine. Then run:

```sh
python -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links bhap-offline-bundle/wheels "bhap-ai[all]"
bhap setup --llm qwen2.5:7b --embedding bge-m3
bhap doctor --offline --verify
```

## Uninstall

`pip uninstall bhap-ai`, then delete the data directory shown by `bhap status` (this removes your index, history and stored copies).
