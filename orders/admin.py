from django.contrib import admin
from .models import Profile, Restaurant, MenuItem, Order, OrderItem


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 1


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "cuisine", "owner", "is_open")
    list_editable = ("is_open",)
    raw_id_fields = ("owner",)
    inlines = [MenuItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer_name", "restaurant", "total", "status", "payment_method", "payment_status", "created_at")
    list_filter = ("status", "payment_method", "payment_status")
    inlines = [OrderItemInline]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "is_owner")
    list_editable = ("is_owner",)
