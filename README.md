# frigate-decide

A sidecar for [Frigate](https://frigate.video) that asks a local Ollama
**decision model** about each detection and only lets real alerts through.

Every Frigate + LLM blueprint we could find makes the model write a *caption*
and then notifies anyway. frigate-decide is the missing half: it asks typed
questions (choice / yes-no / score) that return **an answer with a
probability**, applies your rules to that answer, and gates the notification
on the result.

```
Frigate event (MQTT) -> snapshot -> Ollama /v1/systemone (typed questions)
     -> rules (scene == person_property?) -> MQTT decision + HA webhook
```

**Fail open by design.** If no endpoint answers within the timeout, the event
passes through exactly as un-gated Frigate would have. Silence must never
come from a broken model.

## Why

A typical suburban camera emits hundreds of `car` events a day for vehicles
passing on the street. A decision model answers one typed question - *is the
main subject a person on the property, or a car on the street?* - and the
rules drop the street traffic before your phone buzzes. Pre-filters (label,
score, zone, stationary) drop the obvious noise before it even reaches the
model.

## Quick start (5 minutes)

1. Run an Ollama server with a vision model, e.g. on `gpu-box.lan`:

   ```bash
   ollama pull d1   # or any model your Ollama serves via /v1/systemone
   ```

2. Copy `config.example.yaml` to `config.yaml`, point `decide.endpoints` at
   your Ollama server(s) and `mqtt.host` at your broker. Leave `shadow: true`
   to start.

3. Bring it up next to Frigate:

   ```bash
   docker compose up -d --build
   docker logs -f frigate-decide
   ```

   Decisions appear on MQTT at `frigate_decide/<camera>/decision` (JSON with
   answers, probabilities, model, latency) while your existing alerts keep
   flowing unchanged.

4. When the decisions look right, add the webhook automation from
   `examples/ha-automation.yaml`, set `ha.webhook`, and flip `shadow: false`.

### Score it on your own cameras first

Label a handful of snapshots into the bake-off layout and see what the gate
would have done **before** you trust it:

```
my-eval/
  sample.json   # [{"id", "camera", "label", "score", "zones"}, ...]
  truth.json    # {"scene": ["person_property", "vehicle_street", ...]}
  snaps/        # <id>.jpg - the snapshots
```

```bash
docker compose run --rm frigate-decide \
  eval /data/my-eval --models d1 --endpoint http://gpu-box.lan:11434
# model                     scene  caught missed  false fail-open  median     p95  errors
# d1                         9/10      2      0      0         0     740     802       0
```

A synthetic smoke-test corpus ships in `examples/synthetic-eval/` so you can
verify the plumbing end-to-end against a real Ollama with no camera involved
(`tools/make_synthetic_eval.py` regenerates it).

## Works with no GPU

The sidecar itself is a tiny Python process (one MQTT subscription, one
in-flight model call, ~30 MB of RAM). Inference goes to whatever Ollama
endpoint you list - a GPU box first, a CPU-only box second, tried in order.
A 3B decision model answers in well under a second on a mid-range GPU and in
the tens of seconds on CPU; the single-flight queue with per-camera cooldown
and drop-oldest is what makes the CPU end usable during a burst of passing
cars. Prefer one model kept loaded briefly (`keep_alive: 5m`) on shared
machines.

## Configuration

See `config.example.yaml` - every knob is documented inline. The load-bearing
ones:

- `decide.endpoints` - ordered Ollama base URLs; first answer wins, all-fail
  = fail-open.
- `rules.notify_when` - the gate. Any question, any field, any set of values.
- `prefilter` - labels / min_score / zones / stationary_only, applied before
  the model sees anything.
- `shadow` - decide + publish only, change nothing. **Start here.**

## Design notes

- **Typed decisions, not captions.** Questions are `choice` (one of N
  labelled criteria), `noul` (yes/no), or `score` (0..N); answers carry
  probabilities. The default question set asks for the scene and whether a
  real human is visible.
- **One in-flight request.** The queue is bounded (`queue_max`) and drops the
  *oldest* pending event when a burst arrives - the newest frame is the one
  worth deciding on.
- **Fail-open.** `gate: "fail_open"` in the decision record means no model
  answered; the rules treat it as notify so behaviour degrades to plain
  Frigate.
- **Shadow mode.** The default. Decisions are published to MQTT for you to
  audit against your real alert stream; nothing is suppressed and no webhook
  is called.

## Model results

Reported on the **synthetic** corpus in `examples/synthetic-eval/` only -
never on private camera data. Run it yourself:

```bash
docker compose run --rm frigate-decide \
  eval examples/synthetic-eval --models <your-model> --endpoint http://127.0.0.1:11434
```

Solid-colour squares are not a benchmark; the corpus exists so you can prove
the pipeline works and so the harness output format is visible. Evaluate on
your own labeled snapshots before trusting any model.

## License

MIT - see [LICENSE](LICENSE).
