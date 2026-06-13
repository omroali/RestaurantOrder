# Restaurant Ordering Robot

A lightweight, open-source prototype that acts as a robot waiter:

1. Accepts a spoken (or typed) order from a customer.
2. Converts speech to text (via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)).
3. Extracts menu items, quantities, sizes, modifiers, and corrections using a rule-based parser — **no LLM required**.
4. Repeats the interpreted order back for confirmation.
5. Handles corrections until the customer is satisfied.
6. Outputs the final order as structured JSON.

---

## Architecture

```
Microphone / WAV file
        │
        ▼
  [ audio_input.py ]       – record or load audio
        │
        ▼
  [ speech_to_text.py ]    – faster-whisper ASR  (optional)
        │
        ▼    (or typed text directly)
  [ order_parser.py ]      – clause-based rule parser
        │
        ▼
  [ order_state.py ]       – basket management
        │
        ▼
  [ dialogue_manager.py ]  – state machine: order → confirm → correct → done
        │
        ▼
     Final JSON
```

### Module summary

| File | Purpose |
|---|---|
| `menu.json` | Item catalogue: names, synonyms, categories, options, modifiers |
| `src/models.py` | `OrderItem` and `Order` dataclasses |
| `src/utils.py` | Text normalisation, quantity/size parsing, intent detection |
| `src/order_parser.py` | Clause-based rule parser → `List[OrderItem]` |
| `src/order_state.py` | Basket: add, remove, update, correct, summarise, export JSON |
| `src/dialogue_manager.py` | Conversation state machine |
| `src/speech_to_text.py` | faster-whisper wrapper with graceful fallback |
| `src/audio_input.py` | Microphone recording and WAV loading (pyaudio) |
| `main.py` | CLI entry point (typed, audio-file, microphone modes) |
| `tests/` | pytest unit tests for parser and order state |

---

## Quick start

### Typed-text mode (no extra dependencies)

```bash
cd restaurant_ordering_robot
python main.py
```

### Audio-file mode (requires faster-whisper)

```bash
pip install faster-whisper
python main.py --audio sample_order.wav
```

### Microphone mode (requires faster-whisper + pyaudio)

```bash
pip install faster-whisper pyaudio
python main.py --mic --duration 8
```

### Run tests

```bash
pip install pytest
pytest tests/ -v
```

### Live speech config

The live voice launcher reads `config/speech.yaml` for wake words, Whisper
model, silence timing, confirmation delay, and other speech runtime settings.
The menu still lives in `menu.json`.

```bash
python run_voice.py --speech-config config/speech.yaml
```

---

## Example interaction

```
You: Can I get two cheeseburgers, one without onions, and a large Diet Coke?

[Robot] I have two Cheeseburgers, one without onions, and one large Diet Coke.
        Is that correct?

You: Actually make the Coke regular.

[Robot] Updated. I have two Cheeseburgers, one without onions, and one large Coke.
        Is that correct?

You: Yes

[Robot] {
  "items": [
    { "line_id": 1, "item_id": "cheeseburger",  "display_name": "Cheeseburger",
      "quantity": 2, "size": null, "options": {}, "modifiers": [], "confidence": 1.0 },
    { "line_id": 2, "item_id": "cheeseburger",  "display_name": "Cheeseburger",
      "quantity": 1, "size": null, "options": {}, "modifiers": ["no onions"], "confidence": 0.8 },
    { "line_id": 3, "item_id": "coke",           "display_name": "Coke",
      "quantity": 1, "size": "large", "options": {}, "modifiers": [], "confidence": 1.0 }
  ],
  "status": "confirmed",
  "confirmation_text": "Order confirmed: two Cheeseburgers, one without onions, and one large Coke"
}
```

---

## Parser design

The parser splits the transcript into **clauses** (on commas, "and", "also",
"plus") and for each clause:

1. Finds the **longest-matching** menu item (name or synonym).
2. Scans for a **quantity** word (`a`, `an`, `one` … `ten`, or a digit).
3. Scans for a **size** word (`small`, `medium`, `large`, …).
4. Extracts **modifier phrases** via regex patterns:
   - `no X` / `without X` / `hold (the) X` → `"no X"`
   - `extra X` → `"extra X"`
   - `with X` → `"with X"`
5. Extracts **option values** for required menu options (e.g. `grilled` / `crispy`
   for the Chicken Burger).
6. If a clause has **no menu item** but has modifiers/size, it is attached to
   the most recently mentioned item (handles *"one without onions"* after
   *"two cheeseburgers"*).

---

## Menu schema

`menu.json` is a JSON array.  Each entry supports:

```json
{
  "id": "cheeseburger",
  "name": "Cheeseburger",
  "synonyms": ["cheese burger", "cheeseburgers"],
  "category": "burger",
  "required_options": [],
  "allowed_options": {},
  "allowed_modifiers": ["no onions", "extra cheese", "no pickles"]
}
```

- `required_options` – if non-empty, the robot will ask a clarification question.
- `allowed_options`  – maps option name → list of accepted values.
- `allowed_modifiers` – informational; the parser accepts any well-formed modifier phrase.

---

## Extending the system

| Goal | Where to change |
|---|---|
| Add a menu item | `menu.json` |
| Add a quantity word ("dozen") | `src/utils.py` → `QUANTITY_WORDS` |
| Add a size word | `src/utils.py` → `SIZE_WORDS` |
| Add a modifier pattern | `src/order_parser.py` → `_MODIFIER_PATTERNS` |
| Handle a new correction phrase | `src/utils.py` → `_CORRECTION_TRIGGERS` |
| Replace faster-whisper with whisper.cpp | `src/speech_to_text.py` → `transcribe_file` |
| Add spaCy `PhraseMatcher` | `src/order_parser.py` → `_find_menu_item` |
| Add VAD (silero-vad / webrtcvad) | `src/audio_input.py` → `record_microphone` |
| Persist orders to a database | `src/order_state.py` → `to_json` |

---

## CLI options

```
usage: main.py [--audio WAV_FILE | --mic] [--menu MENU_JSON]
               [--model SIZE] [--duration SECONDS]

optional arguments:
  --audio WAV_FILE   WAV file for initial speech input (requires faster-whisper)
  --mic              Record from microphone (requires pyaudio + faster-whisper)
  --menu MENU_JSON   Path to menu JSON (default: menu.json)
  --model SIZE       Whisper model: tiny|base|small|medium|large-v2 (default: base)
  --duration SECS    Max microphone recording seconds (default: 10)
```

---

## Design constraints (from spec)

- No cloud APIs.
- No LLM for order extraction.
- No heavy chatbot framework.
- Order is **never submitted without confirmation**.
- Core (typed-text) mode requires **zero external dependencies**.
- Every module is independently testable.
