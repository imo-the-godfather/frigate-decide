"""frigate-decide CLI: run the gate, or eval a labeled folder."""
import argparse
import json
import logging
import sys
from pathlib import Path

from . import eval as evalmod
from .bridge import Bridge
from .config import load

DEFAULT_CONFIG = "/etc/frigate-decide/config.yaml"


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=DEFAULT_CONFIG)
    parser = argparse.ArgumentParser(prog="frigate-decide", parents=[common])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="listen on MQTT and gate events", parents=[common])
    eval_p = sub.add_parser("eval", help="score models over a labeled folder",
                            parents=[common])
    eval_p.add_argument("folder")
    eval_p.add_argument("--models", default="d1", help="comma-separated model names")
    eval_p.add_argument("--endpoint", action="append", dest="endpoints",
                        help="Ollama base URL (repeatable; default from config)")
    eval_p.add_argument("--timeout", type=float, default=120.0,
                        help="per-request timeout, seconds (CPU models are slow)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "run":
        cfg = load(args.config)
        logging.getLogger("frigate-decide").info(
            "starting: model=%s shadow=%s endpoints=%s",
            cfg["decide"]["model"], cfg["shadow"], cfg["decide"]["endpoints"])
        Bridge(cfg).run_forever()
        return 0

    cfg = {}
    if Path(args.config).exists():
        cfg = load(args.config)
    endpoints = args.endpoints or cfg.get("decide", {}).get("endpoints") \
        or ["http://127.0.0.1:11434"]
    summaries = evalmod.run(
        args.folder,
        [m.strip() for m in args.models.split(",") if m.strip()],
        endpoints,
        timeout_s=args.timeout,
    )
    out = Path("eval-results.json")
    try:
        out.write_text(json.dumps(summaries, indent=1))
    except OSError:
        import tempfile
        out = Path(tempfile.gettempdir()) / "eval-results.json"
        out.write_text(json.dumps(summaries, indent=1))
        print(f"eval-results.json written to {out} (working directory not writable)")
    return 0
