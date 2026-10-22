# Philips Smart Lock for Home Assistant

A custom integration that shows a Philips smart lock on the Philips Home Access cloud as a `lock` entity and unlocks it through its Wi-Fi gateway.
Tested with the Philips DDL610 behind the Philips Wi-Fi gateway.

## What it does

- The lock state is pushed by the cloud over a WebSocket; nothing is polled and the lock is never woken for a state read.
- `lock.unlock` sends the gateway command the Philips app sends; the state updates from the cloud's push a few seconds later.
- `lock.lock` is refused with an explanation: the lock relocks itself about 5 seconds after unlocking.
- A rejected session is renewed once; when the renewal fails the integration asks for the password again under Settings → Repairs.
- The entity is unavailable while the WebSocket is down.

## Account

The Philips cloud keeps one session per account, and every sign-in signs out the previous session.
Use an account dedicated to Home Assistant: add a family member in the Philips app and sign in here with that account.

## Installation

1. Copy `custom_components/philips_lock` into the `custom_components` directory of your Home Assistant configuration and restart Home Assistant.
2. Add the integration under Settings → Devices & services → Add integration → Philips Smart Lock.
3. Enter the email, the password and the phone country code of the dedicated account, then choose the lock when the account has several.

## Battery

The integration talks to the lock only when you unlock it; every unlock wakes the lock over Bluetooth through the gateway, like an unlock from the app.
Reading the state, signing in and renewing the session only talk to the cloud.

## Credits

The login, request signing, command encryption and WebSocket handshake were reverse-engineered by [rsheptolut](https://github.com/rsheptolut/philips_home_access) (MIT).
