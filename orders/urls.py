from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, owner_views as ov

urlpatterns = [
    path("", views.home, name="home"),
    path("restaurant/<int:pk>/", views.restaurant, name="restaurant"),

    # accounts
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(template_name="orders/login.html",
                                                 redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # cart, checkout, payment
    path("cart/", views.cart, name="cart"),
    path("cart/add/<int:item_id>/", views.add_to_cart, name="add_to_cart"),
    path("cart/remove/<int:item_id>/", views.remove_from_cart, name="remove_from_cart"),
    path("cart/inc/<int:item_id>/", views.cart_change, {"delta": 1}, name="cart_inc"),
    path("cart/dec/<int:item_id>/", views.cart_change, {"delta": -1}, name="cart_dec"),
    path("checkout/", views.checkout, name="checkout"),
    path("pay/<int:pk>/", views.pay, name="pay"),

    # orders
    path("orders/", views.order_history, name="order_history"),
    path("order/<int:pk>/", views.order_detail, name="order_detail"),
    path("order/<int:pk>/status/", views.order_status_api, name="order_status"),
    path("order/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("order/<int:pk>/reorder/", views.reorder, name="reorder"),

    # AI
    path("assistant/", views.assistant, name="assistant"),
    path("api/assistant/", views.assistant_api, name="assistant_api"),
    path("api/cart-suggest/", views.cart_suggest_api, name="cart_suggest"),

    # restaurant owner dashboard
    path("owner/", ov.owner_dashboard, name="owner_dashboard"),
    path("owner/restaurant/", ov.owner_restaurant, name="owner_restaurant"),
    path("owner/toggle-open/", ov.owner_toggle_open, name="owner_toggle_open"),
    path("owner/order/<int:pk>/advance/", ov.owner_order_advance, name="owner_order_advance"),
    path("owner/order/<int:pk>/cancel/", ov.owner_order_cancel, name="owner_order_cancel"),
    path("owner/menu/", ov.owner_menu, name="owner_menu"),
    path("owner/menu/<int:pk>/edit/", ov.owner_item_edit, name="owner_item_edit"),
    path("owner/menu/<int:pk>/toggle/", ov.owner_item_toggle, name="owner_item_toggle"),
    path("owner/menu/<int:pk>/delete/", ov.owner_item_delete, name="owner_item_delete"),
    path("owner/api/insights/", ov.owner_insights_api, name="owner_insights"),
    path("owner/api/describe/", ov.owner_describe_api, name="owner_describe"),
]
