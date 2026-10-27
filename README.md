# textbee

[![PyPI](https://img.shields.io/pypi/v/textbee)](https://pypi.org/project/textbee/)
[![CI](https://github.com/textbee/textbee-python/actions/workflows/ci.yaml/badge.svg)](https://github.com/textbee/textbee-python/actions/workflows/ci.yaml)
[![Python](https://img.shields.io/pypi/pyversions/textbee)](https://pypi.org/project/textbee/)
[![license](https://img.shields.io/pypi/l/textbee)](https://github.com/textbee/textbee-python/blob/main/LICENSE)

Official Python SDK for [textbee.dev](https://textbee.dev), the open source SMS gateway that turns an Android phone into an SMS API.

Zero dependencies, fully typed, works on Python 3.9+.

Writing JavaScript or TypeScript? Use the official [JavaScript SDK](https://github.com/textbee/textbee-js): `npm install @textbee/sdk`.

## Install

```bash
pip install textbee
```

```bash
uv add textbee
```

## Quickstart

Get an API key from the [textbee dashboard](https://textbee.dev/dashboard), then:

```python
import os

from textbee import Textbee

textbee = Textbee(api_key=os.environ["TEXTBEE_API_KEY"])

textbee.send_sms(
    recipients=["+12025550123"],
    message="Hello from textbee!",
)
```

## Send options

`send_sms` needs only `message` and `recipients`. Everything else is optional.

| Option | Type | What it does |
| --- | --- | --- |
| `device_id` | `str` | Which phone sends the message. Omit it and textbee uses your default device, or the enabled device with the most recent heartbeat. |
| `sim_subscription_id` | `int` | Which SIM sends the message on a multi-SIM phone. Omit it and the phone uses its configured preferred SIM, or the system default. |
| `scheduled_at` | `str \| datetime` | Send later instead of now. An ISO 8601 string or a timezone-aware `datetime`, in the future. |

```python
from datetime import datetime, timedelta, timezone

textbee.send_sms(
    recipients=["+12025550123"],
    message="Your appointment is tomorrow at 9am",
    device_id="65f0000000000000000000aa",
    sim_subscription_id=2,
    scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
)
```

A naive `datetime` (one with no `tzinfo`) raises `ValueError`, because the SDK cannot know which timezone you meant.

The response shape depends on how the message was dispatched. A queued send carries a batch id. Narrow with `in`, not `.get()`, so your type checker knows which shape you have:

```python
result = textbee.send_sms(recipients=recipients, message=message)
if "smsBatchId" in result:
    print(result["smsBatchId"], result["recipientCount"])

# Present when the phone last reported a problem, such as the SMS permission off
if "warning" in result:
    print(result["warning"]["message"])
```

### Finding your sim_subscription_id

Open the textbee Android app, go to **Dashboard**, and find the **SIM Cards** section. Each SIM shows its subscription id with a copy button.

Be aware that this value is not validated. If the id does not match a SIM currently in the phone it is ignored, and the message goes out from the preferred or default SIM instead. Nothing errors, so confirm which SIM was used by checking the number the message arrived from.

## Devices

```python
devices = textbee.get_devices()
device = textbee.get_device(device_id)

# Change which device handles sends that omit device_id
textbee.set_default_device(device_id)
```

## Messages and delivery status

History is account-level: one call covers every device, and `device_ids` narrows it.

```python
# Paginated history across the whole account, filterable and searchable
page = textbee.get_messages(
    direction="sent",  # "all" | "sent" | "received"
    device_ids=[device_id],  # omit for every device
    status="delivered",  # delivery state; status="failed" lists failed sends
    search="invoice",
    from_="2026-08-01",  # dates are UTC; datetimes need an explicit timezone
    to="2026-09-01T00:00:00Z",  # exclusive, so windows never double-count
    page=1,
    limit=50,
)
messages, meta = page["data"], page["meta"]

# direction on each message is lowercase and feeds straight back into filters
sent = [m for m in messages if m.get("direction") == "sent"]

# Which recipients of a bulk send failed: filter by the batch a send returned
result = textbee.send_sms(recipients=recipients, message=message)
if "smsBatchId" in result:
    failed = textbee.get_messages(sms_batch_id=result["smsBatchId"], status="failed")

# Drain everything matching a filter: iterate_messages follows the
# pagination cursor for you until there is nothing left. page_size is the
# number fetched per request, not a cap on the total.
for message in textbee.iterate_messages(direction="received", order="asc", page_size=100):
    print(message.get("sender"), message["message"])

# A single message and its current status
sms = textbee.get_sms(device_id, sms_id)

# A whole batch, using the smsBatchId returned by send_sms. This needs the id
# of the device that sent it; when textbee picked the device, use
# get_messages(sms_batch_id=...) instead.
batch_result = textbee.get_sms_batch(device_id, sms_batch_id)
batch, batch_messages = batch_result["batch"], batch_result["messages"]
```

`get_sms` and `get_sms_batch` return the `Sms` type. It differs from the `Message` that `get_messages` returns: `device` is the device id string, not an object with the phone's name and model, and `direction` is absent (read `type`).

The filter is named `from_` because `from` is a Python keyword. It is sent to the API as `from`.

`batch["status"]` and the batch counts come from the messages in the batch, so they change as each message reports. `successCount` counts messages sent or delivered, and `failureCount` counts failed ones. `pendingCount`, `dispatchedCount`, `sentCount`, `deliveredCount` and `unknownCount` break the rest down by state.

Responses are plain dicts typed as `TypedDict`s, so keys keep the API's names (`smsBatchId`, `createdAt`) and your editor completes them.

## Verifying webhooks

textbee signs each webhook delivery with HMAC-SHA256 and sends the hex digest in the `X-Signature` header. Pass the raw request body, not a re-serialized dict, whenever your framework gives you access to it.

```python
import json
import os

from flask import Flask, request
from textbee import verify_webhook_signature

app = Flask(__name__)


@app.post("/webhooks/textbee")
def textbee_webhook():
    valid = verify_webhook_signature(
        payload=request.get_data(),
        signature=request.headers.get("X-Signature"),
        signing_secret=os.environ["TEXTBEE_WEBHOOK_SECRET"],
    )
    if not valid:
        return "", 401

    event = json.loads(request.get_data())
    return "", 200
```

`payload` takes `bytes`, `str`, or a dict. A dict is re-serialized the way the API serializes it, which only matches while key order and number formatting survive the round trip, so prefer the raw body.

## SMS utilities

Pure helpers for working with SMS text and phone numbers. No API key, no network calls, and they are useful with any SMS provider, not just textbee.

### Segments and encoding

Carriers bill per segment, not per message. A message stays in the 7-bit GSM alphabet at 160 characters per segment, but a single character outside that alphabet, one emoji or one curly quote, switches the whole message to UCS-2 and drops the limit to 70.

```python
from textbee import count_sms_segments, find_non_gsm7_characters, get_sms_encoding

count_sms_segments("Your code is 123456")
# SmsSegmentInfo(encoding='gsm-7', length=19, segments=1, remaining_in_segment=141)

count_sms_segments("Your code is 123456 🎉")
# SmsSegmentInfo(encoding='ucs-2', length=22, segments=1, remaining_in_segment=48)

get_sms_encoding("plain ascii")  # 'gsm-7'
find_non_gsm7_characters("Hi 🎉")  # ['🎉']
```

Longer messages are split, and concatenation headers shrink each segment to 153 characters (GSM-7) or 67 (UCS-2). In UCS-2, lengths count UTF-16 code units the way phones do, so an emoji costs 2. `remaining_in_segment` counts single-unit characters, so a two-unit character such as an emoji or `€` may not fit even when it reads as 1.

### Keeping messages in GSM-7

Text pasted from a word processor or a CMS is full of curly quotes, ellipses, and non-breaking spaces. `sanitize_for_gsm7` swaps them for plain equivalents so a message does not silently cost three times as much.

```python
from textbee import count_sms_segments, sanitize_for_gsm7

pasted = "“Your order shipped…”"
count_sms_segments(pasted).encoding  # 'ucs-2'

clean = sanitize_for_gsm7(pasted)  # '"Your order shipped..."'
count_sms_segments(clean).encoding  # 'gsm-7'

# Optionally strip accents that GSM-7 does not carry. Letters it does carry,
# like é, ü, and ñ, are always left alone.
sanitize_for_gsm7("naïve", transliterate_accents=True)  # 'naive'
```

It is best effort: characters with no safe equivalent pass through untouched. Check the result with `get_sms_encoding` and see what is left with `find_non_gsm7_characters`.

### Phone number helpers

```python
from textbee import is_valid_e164, normalize_phone_number

is_valid_e164("+12025550123")  # True
is_valid_e164("202-555-0123")  # False

normalize_phone_number("+1 (202) 555-0123")  # '+12025550123'
normalize_phone_number("0012025550123")  # '+12025550123'
normalize_phone_number("(202) 555-0123", default_country_code="1")  # '+12025550123'
normalize_phone_number("not a number")  # None
```

These are format-only helpers, not [phonenumbers](https://pypi.org/project/phonenumbers/). They know nothing about country dialing plans, so a well-formed but unassigned number still passes. Input that cannot be normalized returns `None`; an unusable `default_country_code` raises `ValueError`.

## Errors

Any non-2xx response raises a `TextbeeError` carrying the status and the parsed body. So does a redirect (the client never follows one, so your key never goes to another host) and a response that is not the API's JSON. Both usually mean a wrong `base_url`.

Network failures raise the underlying error instead: an `OSError` such as `URLError` or a socket timeout, or an `http.client` error when the connection drops.

```python
from textbee import TextbeeError

try:
    textbee.send_sms(recipients=["+12025550123"], message="hi")
except TextbeeError as error:
    print(error.status, error.message, error.body)
```

## Client options

```python
Textbee(
    api_key="your-api-key",
    base_url="https://api.textbee.dev/api/v1",  # the default
    timeout=30.0,  # seconds for each connect and each read
)
```

### Self-hosted textbee

Point `base_url` at your own instance. A bare origin gets `/api/v1` added, so all of these reach the same API:

```python
Textbee(api_key=api_key, base_url="https://sms.example.com")
Textbee(api_key=api_key, base_url="https://sms.example.com/api/v1")
Textbee(api_key=api_key, base_url="http://localhost:3001")
```

If the API sits under a path prefix, give the full path, for example `https://example.com/textbee/api/v1`. Any path other than `/api/v1` is used as given, so a proxy that maps `/sms` onto the API works with `https://example.com/sms`. A value with no scheme gets `https://`. An invalid URL, or one with a username or password, raises `ValueError` instead of falling back to the hosted API, so a typo never sends your key to textbee.dev.

## What is not covered yet

The SDK focuses on sending and reading messages. Bulk send and a few device operations are still REST only, documented at [textbee.dev/docs](https://textbee.dev/docs). The client is synchronous; call it from a thread (`asyncio.to_thread`) inside async code.

## Community and support

Questions or feedback? Join the community on [Discord](https://textbee.dev/discord) or email support@textbee.dev.

## License

MIT. Part of the [textbee](https://github.com/textbee/textbee) project.
