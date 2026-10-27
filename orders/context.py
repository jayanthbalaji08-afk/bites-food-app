from decimal import Decimal

from .models import MenuItem
from .utils import is_owner


def cart_summary(request):
    cart = request.session.get("cart", {})
    count, total = 0, Decimal("0")
    ids = [int(k) for k in cart if str(k).isdigit()]
    if ids:
        for i in MenuItem.objects.filter(pk__in=ids):
            qty = cart.get(str(i.pk), 0)
            count += qty
            total += i.price * qty
    return count, total


def cart_info(request):
    count, total = cart_summary(request)
    return {
        "cart_count": count,
        "cart_total": total,
        "user_is_owner": is_owner(request.user) if request.user.is_authenticated else False,
    }
