import json
import math
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from . import ai, payments
from .context import cart_summary
from .forms import CheckoutForm, SignupForm
from .geo import distance_km
from .models import MenuItem, Order, OrderItem, Restaurant
from .utils import get_profile, throttle

MAX_QTY = 20
FLOW = [("placed", "Order placed"), ("preparing", "Preparing"),
        ("on_the_way", "On the way"), ("delivered", "Delivered")]


# ------------------------------------------------------------------ helpers
def _parse_coords(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def _nearby(coords, limit=None, only_open=False):
    qs = Restaurant.objects.prefetch_related("items")
    if only_open:
        qs = qs.filter(is_open=True)
    restaurants = list(qs)
    for r in restaurants:
        r.distance = r.eta = None
        if coords:
            r.distance = round(distance_km(coords[0], coords[1], r.latitude, r.longitude), 1)
            r.eta = 15 + round(r.distance * 4)  # rough estimate
    restaurants.sort(key=lambda r: (not r.is_open, r.distance or 0, r.name))
    return restaurants[:limit] if limit else restaurants


def _safe_next(request, nxt):
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    return reverse("home")


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_body(request):
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _item_json(item):
    return {"id": item.id, "name": item.name, "price": f"{item.price:.0f}" if item.price == int(item.price) else f"{item.price:.2f}",
            "restaurant": item.restaurant.name, "restaurant_id": item.restaurant_id,
            "is_veg": item.is_veg, "image": item.picture_url}


# ------------------------------------------------------------------ accounts
def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Welcome, {user.username}!")
        if get_profile(user).is_owner:
            messages.info(request, "Set up your restaurant to start receiving orders.")
            return redirect("owner_restaurant")
        return redirect(_safe_next(request, nxt))
    return render(request, "orders/signup.html", {"form": form, "next": nxt})


# ------------------------------------------------------------------ browsing
def home(request):
    coords = _parse_coords(request.GET.get("lat"), request.GET.get("lng"))
    if coords:
        request.session["coords"] = list(coords)
    return render(request, "orders/home.html", {"restaurants": _nearby(coords), "located": coords is not None})


def restaurant(request, pk):
    r = get_object_or_404(Restaurant, pk=pk)
    cart = request.session.get("cart", {})
    items = list(r.items.all())
    for i in items:
        i.qty = cart.get(str(i.pk), 0)
    return render(request, "orders/restaurant.html", {"r": r, "items": items})


# ------------------------------------------------------------------ cart
@require_POST
def add_to_cart(request, item_id):
    item = get_object_or_404(MenuItem.objects.select_related("restaurant"), pk=item_id)
    back = reverse("restaurant", args=[item.restaurant_id]) + f"#item-{item.pk}"

    def fail(msg):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "message": msg}, status=400)
        messages.error(request, msg)
        return redirect(back)

    if not item.restaurant.is_open:
        return fail(f"{item.restaurant.name} is closed right now.")
    if not item.available:
        return fail(f"{item.name} is sold out.")

    cart = dict(request.session.get("cart", {}))
    note = ""
    ids = [int(k) for k in cart if str(k).isdigit()]
    if ids and MenuItem.objects.filter(pk__in=ids).exclude(restaurant_id=item.restaurant_id).exists():
        cart = {}
        note = "Your cart was cleared because you can order from one restaurant at a time."
    cart[str(item.pk)] = min(cart.get(str(item.pk), 0) + 1, MAX_QTY)
    request.session["cart"] = cart

    if _is_ajax(request):
        return JsonResponse({"ok": True, "message": note or f"{item.name} added.", "count": cart_summary(request)[0]})
    if note:
        messages.info(request, note)
    if request.POST.get("next") == "cart":
        return redirect("cart")
    return redirect(back)


@require_POST
def cart_change(request, item_id, delta):
    cart = dict(request.session.get("cart", {}))
    key = str(item_id)
    if key in cart:
        cart[key] = min(cart[key] + delta, MAX_QTY)
        if cart[key] <= 0:
            del cart[key]
    request.session["cart"] = cart
    return redirect("cart")


@require_POST
def remove_from_cart(request, item_id):
    cart = dict(request.session.get("cart", {}))
    cart.pop(str(item_id), None)
    request.session["cart"] = cart
    return redirect("cart")


def _cart_lines(request):
    cart = request.session.get("cart", {})
    ids = [int(k) for k in cart if str(k).isdigit()]
    items = MenuItem.objects.filter(pk__in=ids).select_related("restaurant").order_by("pk")
    lines, total = [], Decimal("0")
    for i in items:
        qty = cart[str(i.pk)]
        sub = i.price * qty
        total += sub
        lines.append({"item": i, "qty": qty, "subtotal": sub, "ok": i.available and i.restaurant.is_open})
    return lines, total


def cart(request):
    lines, total = _cart_lines(request)
    return render(request, "orders/cart.html", {
        "lines": lines, "total": total, "blocked": any(not l["ok"] for l in lines)})


# ------------------------------------------------------------------ checkout & payment
@login_required
@require_http_methods(["GET", "POST"])
def checkout(request):
    lines, total = _cart_lines(request)
    if not lines:
        messages.info(request, "Your cart is empty.")
        return redirect("cart")
    if len({l["item"].restaurant_id for l in lines}) > 1:
        request.session["cart"] = {}
        messages.error(request, "Your cart had dishes from different restaurants, so it was cleared.")
        return redirect("home")
    if any(not l["ok"] for l in lines):
        messages.error(request, "Some dishes are sold out or the restaurant is closed. Remove them to continue.")
        return redirect("cart")
    restaurant_obj = lines[0]["item"].restaurant

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            online = cd["payment_method"] == "online"
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user, restaurant=restaurant_obj, customer_name=cd["name"],
                    phone=cd["phone"], address=cd["address"], total=total,
                    payment_method=cd["payment_method"],
                    status="pending_payment" if online else "placed",
                    dest_lat=cd.get("lat"), dest_lng=cd.get("lng"))
                OrderItem.objects.bulk_create([
                    OrderItem(order=order, item=l["item"], quantity=l["qty"], price=l["item"].price)
                    for l in lines])
            profile = get_profile(request.user)
            if not profile.phone or not profile.default_address:
                profile.phone = profile.phone or cd["phone"]
                profile.default_address = profile.default_address or cd["address"]
                profile.save()
            request.session["cart"] = {}
            if online:
                return redirect("pay", pk=order.pk)
            messages.success(request, "Order placed. The restaurant has been notified.")
            return redirect("order_detail", pk=order.pk)
    else:
        profile = get_profile(request.user)
        coords = request.session.get("coords") or [None, None]
        form = CheckoutForm(initial={
            "name": request.user.get_full_name() or request.user.username,
            "phone": profile.phone, "address": profile.default_address,
            "payment_method": "cod", "lat": coords[0], "lng": coords[1]})
    return render(request, "orders/checkout.html", {
        "form": form, "lines": lines, "total": total, "restaurant": restaurant_obj})


@login_required
@require_http_methods(["GET", "POST"])
def pay(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if order.status != "pending_payment" or order.payment_method != "online":
        return redirect("order_detail", pk=order.pk)
    error = None
    if request.method == "POST":
        with transaction.atomic():
            fresh = Order.objects.select_for_update().get(pk=order.pk)
            if fresh.status != "pending_payment":
                return redirect("order_detail", pk=order.pk)
            ok, result = payments.charge(fresh, request.POST)
            if ok:
                fresh.status, fresh.payment_status, fresh.payment_ref = "placed", "paid", result
                fresh.save()
                messages.success(request, "Payment successful. Your order is confirmed.")
                return redirect("order_detail", pk=order.pk)
            fresh.payment_status = "failed"
            fresh.save()
            error = result
    return render(request, "orders/pay.html", {"order": order, "error": error})


# ------------------------------------------------------------------ orders
def _load_order(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("restaurant").prefetch_related("lines__item"), pk=pk)
    u = request.user
    if not (order.user_id == u.id or order.restaurant.owner_id == u.id or u.is_staff):
        raise Http404
    return order


def _steps(order):
    if order.status in ("pending_payment", "cancelled"):
        return []
    idx = [k for k, _ in FLOW].index(order.status)
    return [{"label": label, "done": n <= idx} for n, (_, label) in enumerate(FLOW)]


def _eta_text(order):
    if order.status == "delivered":
        return "Delivered"
    if order.status == "cancelled":
        return "Order cancelled"
    if order.status == "pending_payment":
        return "Waiting for payment"
    mins = order.eta_minutes()
    if order.status == "on_the_way":
        return "Arriving in less than a minute" if mins <= 0 else f"Arriving in about {mins} min"
    return f"Estimated delivery in about {mins} min"


@login_required
def order_detail(request, pk):
    order = _load_order(request, pk)
    dest = order.destination
    return render(request, "orders/order.html", {
        "order": order, "steps": _steps(order), "eta_text": _eta_text(order),
        "show_map": order.status in ("placed", "preparing", "on_the_way", "delivered"),
        "rest_lat": order.restaurant.latitude, "rest_lng": order.restaurant.longitude,
        "dest_lat": dest[0], "dest_lng": dest[1], "rider": order.rider_position(),
        "is_customer": order.user_id == request.user.id})


def order_status_api(request, pk):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login"}, status=401)
    order = _load_order(request, pk)
    lat, lng = order.rider_position()
    return JsonResponse({
        "status": order.status, "status_label": order.get_status_display(),
        "payment_status": order.get_payment_status_display(),
        "steps": _steps(order), "eta_text": _eta_text(order),
        "rider": {"lat": lat, "lng": lng}, "progress": round(order.progress(), 3),
        "final": order.status in ("delivered", "cancelled")})


@login_required
def order_history(request):
    orders = (request.user.orders.select_related("restaurant").prefetch_related("lines__item"))
    return render(request, "orders/orders.html", {"orders": orders})


@login_required
@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if not order.can_cancel_by_customer:
        messages.error(request, "This order can no longer be cancelled because the restaurant has started on it.")
    else:
        was_paid = order.payment_status == "paid"
        order.cancel()
        messages.success(request, "Order cancelled." + (" Your payment will be refunded." if was_paid else ""))
    return redirect("order_detail", pk=order.pk)


@login_required
@require_POST
def reorder(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related("lines__item__restaurant"), pk=pk, user=request.user)
    cart, skipped = {}, 0
    for line in order.lines.all():
        item = line.item
        if item.available and item.restaurant.is_open:
            cart[str(item.pk)] = min(line.quantity, MAX_QTY)
        else:
            skipped += 1
    request.session["cart"] = cart
    if not cart:
        messages.error(request, "Those dishes are not available right now.")
        return redirect("order_history")
    if skipped:
        messages.info(request, f"{skipped} dish(es) are unavailable and were skipped.")
    return redirect("cart")


# ------------------------------------------------------------------ AI endpoints
def assistant(request):
    return render(request, "orders/assistant.html", {"ai_on": ai.ai_enabled()})


@require_POST
def assistant_api(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"reply": "Invalid request."}, status=400)
    message = str(data.get("message") or "").strip()[:300]
    if not message:
        return JsonResponse({"reply": "Tell me what you feel like eating.", "items": []})
    if throttle(request, "chat", limit=12, window=60):
        return JsonResponse({"reply": "You are asking quickly. Wait a few seconds and try again.", "items": []}, status=429)
    coords = _parse_coords(data.get("lat"), data.get("lng")) or (request.session.get("coords") and tuple(request.session["coords"]))
    restaurants = _nearby(coords or None, limit=5, only_open=True)
    items = []
    for r in restaurants:
        for i in r.items.all():
            i.restaurant = r  # avoids one extra query per dish
            if i.available:
                items.append(i)
    favourites = []
    if request.user.is_authenticated:
        favourites = [row["item__name"] for row in
                      OrderItem.objects.filter(order__user=request.user).exclude(order__status__in=["cancelled", "pending_payment"])
                      .values("item__name").annotate(q=Sum("quantity")).order_by("-q")[:3]]
    result = ai.chat(message, data.get("history"), items, favourites)
    return JsonResponse({"reply": result["reply"], "mode": result["mode"],
                         "items": [_item_json(i) for i in result["items"]]})


def cart_suggest_api(request):
    lines, _ = _cart_lines(request)
    if not lines:
        return JsonResponse({"suggestions": []})
    restaurant_obj = lines[0]["item"].restaurant
    in_cart = {l["item"].id for l in lines}
    candidates = list(restaurant_obj.items.filter(available=True).exclude(pk__in=in_cart).select_related("restaurant"))
    sig = ",".join(f"{l['item'].id}x{l['qty']}" for l in lines)
    cached = request.session.get("sug")
    if cached and cached.get("sig") == sig:
        wanted = {s["id"]: s["reason"] for s in cached["data"]}
        by_id = {i.id: i for i in candidates}
        picks = [{"item": by_id[i], "reason": r} for i, r in wanted.items() if i in by_id]
    else:
        allow_ai = not throttle(request, "suggest", limit=8, window=60)
        picks = ai.suggest_for_cart([l["item"] for l in lines], candidates, allow_ai=allow_ai)
        request.session["sug"] = {"sig": sig, "data": [{"id": p["item"].id, "reason": p["reason"]} for p in picks]}
    return JsonResponse({"suggestions": [dict(_item_json(p["item"]), reason=p["reason"]) for p in picks]})
