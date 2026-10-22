"""Protocol constants of the Philips Home Access cloud.

Every value is a fixed, reverse-engineered fact from rsheptolut's
philips_home_access (MIT), verified live with a DDL610 on 2026-10-22.
"""

# Auth backend; the same host for every account.
AUTH_BASE = "https://user-oneness.juziwulian.com"
LOGIN_PATH = "/homeaccess/oauth/login"
DATACENTERS_PATH = "/datacenters"

# Per-datacenter device API; the host comes from POST /datacenters.
DEVICE_LIST_PATH = "/homeaccess/device/list"
GATEWAY_OPEN_PATH = "/v3/gateway/set-lock-open"

# The login reply names datacenters by code; a device record names its home
# datacenter in a different form ("north-america"). Codes not derivable by the
# "Philips" + capitalized-parts rule are listed here.
DATACENTER_ALIASES = {"southeast-asia": "PhilipsSingapore"}

LANGUAGE = "en_US"
LOGIN_USER_AGENT = "okhttp/4.10.0"
DEVICE_USER_AGENT = "System: Android 10/ Model: ONEPLUS A6010/ Brand: OnePlus"
DEVICE_HEADERS = {
    "User-Agent": DEVICE_USER_AGENT,
    "Accept": "application/json",
    "k-tenant": "philips",
    "k-version": "4.14.0",
    "k-signv": "1.0.0",
    "k-language": LANGUAGE,
}
TOKEN_HEADER = "token"
ENCRYPT_DATA_HEADER = "encrypt_data"

# The cloud rejects a displaced or expired token with HTTP 200 and this code.
AUTH_FAIL_CODE = "444"

OPEN_STATUS_LOCKED = 1
OPEN_STATUS_UNLOCKED = 2

# WebSocket keep-alive and reconnect timing (seconds).
WS_HEARTBEAT = 5
RECONNECT_DELAY = 3
RECONNECT_DELAY_MAX = 300
STABLE_AFTER = 60

# Minimum spacing of background re-logins. The cloud keeps one session per
# account, so a re-login kicks out whoever displaced us; spacing them avoids
# a sign-in tug of war with a phone that uses the same account.
RELOGIN_MIN_INTERVAL = 600

# App private key: signs every request. Static and shared by every install of
# the Philips app; not a per-user secret.
APP_PRIVATE_KEY_DER_B64 = "MIICdwIBADANBgkqhkiG9w0BAQEFAASCAmEwggJdAgEAAoGBAOlqfLIYbh+4br4o/spjYE8UwNmeJznE7lPT8TaksfB7FpeiGp5WjR95y6/Z4Rm/V96XyphhUTi0VrkH9lVXL+gX5E31naefJBt5SWLA1fb12jpPArBp9jIWUD16wI0ExfJcYjIOhG4WAf53CwP6Q8wzPbzPkMn4xruedUQwVfUvAgMBAAECgYEA4DogscGQQL6e++RL50aR5UYtgKBSVEefHz5R0Uljen30FRRvd73zcdJB3ptyh5ZtpfKxd7K9ILj1Omiwtgi8heaN68ssiJAl9DjmhR+CegUCkIMll/Plos/wsv/H9/8JQyfbM95+At0hzyIGicpXBpTuFanxAe3qKZKXT7k1zBkCQQD0d5NuIRMrhNj8BjL5My3AYQw3JzxKA8MlVv14UEBsKDcgMMtkickNQ5+K4Lvu51W2qI7ly/Rl0Q4NpIE44iVNAkEA9G1yauyBRblbA3h8t7GUukeDNaA+iZtIQKNLH+99J88GpcvqjwUswvKOYnl8JFMimH2qcDzUkewi9KzjQCrWawJAb92LzBg8cmyO8fxQNPIzXFXMRiyhDOld0edVg0mNwTBB0WwiljXqlzQ7fExMEw0ujq/g+8xxYGniOWHuc74f/QJAUgtCsp08LxkucZXJ1ybmUziZ1DA7jZjvwbKODuQmUGxvQMuXqfYEtlMQdAFvKAo3vJPB1/azK1/lw9ccWHeIjQJBAPQ1WYweAJAiS7Srq4Z/55zr5Cngvd71YBPdrmdzENFlpx4+Ro4awOiZOQvcj8yFb8wU5qR87FOa3MtwSTQ08IE="
# Server public key: encrypts the body of lock commands.
SERVER_PUBLIC_KEY_DER_B64 = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDAhvfVLGrJ/M3xpUnT1xlN30E1UESxhAmGmFyTx3p3vpxF4zMYpUjwHckCvg/zvZwhNTgsm3CNT7LAdE8lCl2YK4BoUZ6IYbbXSOa02/brASX4kjpOPbTcaDfYud2CFWQba95d5dlf3Jf9Z3eTPwNK7YQ0LDDWMOQ6LxoGqcLciQIDAQAB"
