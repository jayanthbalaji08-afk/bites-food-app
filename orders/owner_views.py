from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import ExtractHour
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai
from .forms import MenuItemForm, RestaurantForm
from .models import MenuItem, Order, OrderItem
from .utils import get_owner_restaurant, get_profile, is_owner, throttle
from .views import _json_body

ACTION_LABEL = {"placed": "Accept and start preparing", "preparing": "Send out for delivery",
                "on_the_way": "Mark delivered"}


def owner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        restaurant = get_owner_restaurant(request.user)
        if restaurant is None:
            if get_profile(request.user).is_owner:
                messages.info(request, "Set up your restaurant first.")
                return redirect("owner_restaurant")
            messages.error(request, "That area is for restaurant owners.")
            return redirect("home")
        request.restaurant = restaurant
        return view(request, *args, **kwargs)
    return login_required(wrapper)


@login_required
def owner_restaurant(request):
    if not is_owner(request.user):
        messages.error(request, "That area is for restaurant owners.")
        return redirect("home")
    instance = get_owner_restaurant(request.user)
    form = RestaurantForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.owner = request.user
        r.save()
        messages.success(request, "Restaurant saved.")
        return redirect("owner_dashboard")
    return render(request, "orders/owner_restaurant.html", {"form": form, "creating": instance is None})


@owner_required
def owner_dashboard(request):
    r = request.restaurant
    base = r.orders.exclude(status="pending_payment")
    active = list(base.exclude(status__in=["delivered", "cancelled"]).order_by("created_at")
                  .select_related("restaurant").prefetch_related("lines__item"))
    for o in active:
        o.action_label = ACTION_LABEL.get(o.status)
    recent = base.filter(status__in=["delivered", "cancelled"]).order_by("-created_at")[:8]
    todays = base.filter(created_at__date=timezone.localdate()).exclude(status="cancelled")
    stats = {"orders": todays.count(), "revenue": todays.aggregate(s=Sum("total"))["s"] or 0}
    return render(request, "orders/owner_dashboard.html", {"r": r, "active": active, "recent": recent, "stats": stats})


@owner_required
@require_POST
def owner_order_advance(request, pk):
    order = get_object_or_404(Order, pk=pk, restaurant=request.restaurant)
    if order.status != request.POST.get("from"):
        messages.info(request, f"Order #{order.pk} was already updated.")
    elif order.advance():
        messages.success(request, f"Order #{order.pk} is now {order.get_status_display().lower()}.")
    else:
        messages.error(request, "This order cannot be moved forward.")
    return redirect("owner_dashboard")


@owner_required
@require_POST
def owner_order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk, restaurant=request.restaurant)
    if order.can_cancel_by_owner:
        order.cancel()
        messages.success(request, f"Order #{order.pk} was cancelled." +
                         (" The customer will be refunded." if order.payment_status == "refunded" else ""))
    else:
        messages.error(request, "This order can no longer be cancelled.")
    return redirect("owner_dashboard")


@owner_required
@require_POST
def owner_toggle_open(request):
    r = request.restaurant
    r.is_open = not r.is_open
    r.save()
    messages.success(request, "You are now open for orders." if r.is_open else "You are now closed. Customers cannot order.")
    return redirect("owner_dashboard")


@owner_required
def owner_menu(request):
    r = request.restaurant
    form = MenuItemForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.restaurant = r
        item.save()
        messages.success(request, f"{item.name} added to your menu.")
        return redirect("owner_menu")
    return render(request, "orders/owner_menu.html", {"r": r, "items": r.items.order_by("name"), "form": form})


@owner_required
def owner_item_edit(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.restaurant)
    form = MenuItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Dish updated.")
        return redirect("owner_menu")
    return render(request, "orders/owner_item_form.html", {"form": form, "item": item})


@owner_required
@require_POST
def owner_item_toggle(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.restaurant)
    item.available = not item.available
    item.save()
    return redirect("owner_menu")


@owner_required
@require_POST
def owner_item_delete(request, pk):
    item = get_object_or_404(MenuItem, pk=pk, restaurant=request.restaurant)
    try:
        item.delete()
        messages.success(request, "Dish deleted.")
    except ProtectedError:
        item.available = False
        item.save()
        messages.info(request, "This dish is part of past orders, so it was marked unavailable instead of deleted.")
    return redirect("owner_menu")


# ------------------------------------------------------------------ owner AI
def _week_stats(restaurant):
    since = timezone.now() - timedelta(days=7)
    qs = restaurant.orders.filter(created_at__gte=since).exclude(status__in=["pending_payment", "cancelled"])
    count = qs.count()
    revenue = float(qs.aggregate(s=Sum("total"))["s"] or 0)
    top = [(row["item__name"], row["q"]) for row in
           OrderItem.objects.filter(order__in=qs).values("item__name").annotate(q=Sum("quantity")).order_by("-q")[:3]]
    peak = qs.annotate(h=ExtractHour("created_at")).values("h").annotate(c=Count("id")).order_by("-c").first()
    return {"orders": count, "revenue": revenue, "avg": revenue / count if count else 0,
            "cancelled": restaurant.orders.filter(created_at__gte=since, status="cancelled").count(),
            "top": top, "peak_hour": peak["h"] if peak else None,
            "sold_out_dishes": restaurant.items.filter(available=False).count()}


@owner_required
@require_POST
def owner_insights_api(request):
    if throttle(request, "insights", limit=5, window=60):
        return JsonResponse({"text": "Please wait a moment before asking again."}, status=429)
    return JsonResponse({"text": ai.owner_insights(_week_stats(request.restaurant))})


@owner_required
@require_POST
def owner_describe_api(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        return JsonResponse({"error": "Type the dish name first."}, status=400)
    if throttle(request, "describe", limit=10, window=60):
        return JsonResponse({"error": "Please wait a moment and try again."}, status=429)
    r = request.restaurant
    return JsonResponse({"text": ai.describe_dish(name, bool(data.get("is_veg", True)), r.name, r.cuisine)})
