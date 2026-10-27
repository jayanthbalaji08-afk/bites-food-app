"""Demo payment gateway - NO REAL MONEY MOVES.

Card details are validated and then thrown away. They are never stored or logged.

To use a real gateway (for example Razorpay) later, replace `charge()` with a function that
creates a payment with the gateway, verifies the signature it sends back, and returns
(True, payment_id) or (False, "reason"). The rest of the app does not need to change.
"""
import re
import uuid

from django.utils import timezone

DECLINED_TEST_CARD = "4000000000000002"
UPI_RE = re.compile(r"^[\w.\-]{2,}@[A-Za-z]{2,}$")
EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])\s*/\s*(\d{2})$")


def luhn_ok(number):
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def charge(order, data):
    """Returns (True, reference) on success or (False, error message)."""
    mode = data.get("mode")
    if mode == "upi":
        vpa = (data.get("upi") or "").strip()
        if not UPI_RE.match(vpa):
            return False, "Enter a valid UPI ID, like name@bank."
        return True, "DEMO-" + uuid.uuid4().hex[:12].upper()

    if mode == "card":
        number = re.sub(r"[\s-]", "", data.get("card_number") or "")
        if not (number.isdigit() and 13 <= len(number) <= 19 and luhn_ok(number)):
            return False, "That card number is not valid."
        m = EXPIRY_RE.match((data.get("expiry") or "").strip())
        if not m:
            return False, "Enter the expiry as MM/YY."
        month, year = int(m.group(1)), 2000 + int(m.group(2))
        today = timezone.localdate()
        if (year, month) < (today.year, today.month):
            return False, "This card has expired."
        cvv = (data.get("cvv") or "").strip()
        if not (cvv.isdigit() and len(cvv) in (3, 4)):
            return False, "Enter the 3 or 4 digit CVV."
        if number == DECLINED_TEST_CARD:
            return False, "The bank declined this card. Try another payment method."
        return True, "DEMO-" + uuid.uuid4().hex[:12].upper()

    return False, "Choose a payment method."
