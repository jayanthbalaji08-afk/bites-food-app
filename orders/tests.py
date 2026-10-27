import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai, payments
from .models import MenuItem, Order, OrderItem, Profile, Restaurant


def make_data():
    owner = User.objects.create_user("boss", password="pw12345678")
    Profile.objects.create(user=owner, is_owner=True)
    r1 = Restaurant.objects.create(name="Spice Hub", cuisine="Indian", latitude=12.93, longitude=77.62, owner=owner)
    r2 = Restaurant.objects.create(name="Pizza Spot", cuisine="Italian", latitude=12.95, longitude=77.64)
    biryani = MenuItem.objects.create(restaurant=r1, name="Chicken Biryani", description="Spicy", price=300, is_veg=False)
    paneer = MenuItem.objects.create(restaurant=r1, name="Paneer Tikka", description="Smoky", price=180, is_veg=True)
    coffee = MenuItem.objects.create(restaurant=r1, name="Filter Coffee", description="Hot", price=40, is_veg=True)
    pizza = MenuItem.objects.create(restaurant=r2, name="Margherita Pizza", description="Cheesy", price=250, is_veg=True)
    return owner, r1, r2, biryani, paneer, coffee, pizza


class Base(TestCase):
    def setUp(self):
        self.owner, self.r1, self.r2, self.biryani, self.paneer, self.coffee, self.pizza = make_data()
        self.user = User.objects.create_user("asha", password="pw12345678")
        self.client.login(username="asha", password="pw12345678")

    def add(self, item, times=1):
        for _ in range(times):
            self.client.post(reverse("add_to_cart", args=[item.pk]))

    def place_cod(self, **extra):
        self.add(self.biryani, 2)
        data = {"name": "Asha", "phone": "9876543210", "address": "12 MG Road, Bengaluru",
                "payment_method": "cod", "lat": "12.94", "lng": "77.63"}
        data.update(extra)
        return self.client.post(reverse("checkout"), data)


class AccountTests(TestCase):
    def test_signup_logs_in_and_creates_profile(self):
        r = self.client.post(reverse("signup"), {"username": "newbie", "password1": "Str0ng!pass99",
                                                  "password2": "Str0ng!pass99", "phone": "98765 43210"})
        self.assertRedirects(r, reverse("home"))
        u = User.objects.get(username="newbie")
        self.assertEqual(u.profile.phone, "9876543210")
        self.assertFalse(u.profile.is_owner)
        self.assertEqual(self.client.get(reverse("order_history")).status_code, 200)

    def test_owner_signup_goes_to_restaurant_setup(self):
        r = self.client.post(reverse("signup"), {"username": "chef", "password1": "Str0ng!pass99",
                                                  "password2": "Str0ng!pass99", "is_owner": "on"})
        self.assertRedirects(r, reverse("owner_restaurant"))

    def test_signup_bad_phone_and_weak_password(self):
        r = self.client.post(reverse("signup"), {"username": "x", "password1": "123", "password2": "123", "phone": "12"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_login_redirects_back_and_blocks_open_redirect(self):
        User.objects.create_user("u1", password="pw12345678")
        r = self.client.post(reverse("login") + "?next=/orders/", {"username": "u1", "password": "pw12345678", "next": "/orders/"})
        self.assertRedirects(r, "/orders/")
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "u1", "password": "pw12345678", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_logout_is_post(self):
        User.objects.create_user("u2", password="pw12345678")
        self.client.login(username="u2", password="pw12345678")
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertRedirects(self.client.get(reverse("order_history")), "/login/?next=/orders/")


class BrowseAndCartTests(Base):
    def test_pages_render(self):
        for url in ["/", "/?lat=12.93&lng=77.62", f"/restaurant/{self.r1.pk}/", "/cart/", "/assistant/", "/orders/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_bad_coordinates_are_ignored(self):
        for q in ["?lat=nan&lng=1", "?lat=abc&lng=1", "?lat=999&lng=1", "?lat=1"]:
            self.assertEqual(self.client.get("/" + q).status_code, 200, q)

    def test_nearest_first_and_closed_last(self):
        r = self.client.get("/?lat=12.95&lng=77.64")
        self.assertEqual([x.name for x in r.context["restaurants"]], ["Pizza Spot", "Spice Hub"])
        self.r2.is_open = False
        self.r2.save()
        r = self.client.get("/?lat=12.95&lng=77.64")
        self.assertEqual(r.context["restaurants"][-1].name, "Pizza Spot")

    def test_cart_add_change_and_single_restaurant(self):
        self.add(self.biryani, 2)
        self.assertEqual(self.client.session["cart"], {str(self.biryani.pk): 2})
        self.client.post(reverse("cart_dec", args=[self.biryani.pk]))
        self.client.post(reverse("cart_dec", args=[self.biryani.pk]))
        self.assertEqual(self.client.session["cart"], {})
        self.add(self.biryani)
        self.add(self.pizza)  # other restaurant clears cart
        self.assertEqual(self.client.session["cart"], {str(self.pizza.pk): 1})

    def test_quantity_cap(self):
        self.add(self.coffee, 25)
        self.assertEqual(self.client.session["cart"][str(self.coffee.pk)], 20)

    def test_cannot_add_sold_out_or_closed(self):
        self.biryani.available = False
        self.biryani.save()
        r = self.client.post(reverse("add_to_cart", args=[self.biryani.pk]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 400)
        self.r2.is_open = False
        self.r2.save()
        self.add(self.pizza)
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_ajax_add_returns_count(self):
        r = self.client.post(reverse("add_to_cart", args=[self.coffee.pk]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.json()["count"], 1)

    def test_stale_cart_item_does_not_crash(self):
        s = self.client.session
        s["cart"] = {"99999": 2, "abc": 1}
        s.save()
        self.assertEqual(self.client.get("/cart/").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_dish_added_after_blocked_item_in_cart(self):
        self.add(self.biryani)
        self.biryani.available = False
        self.biryani.save()
        r = self.client.get("/cart/")
        self.assertTrue(r.context["blocked"])
        self.assertRedirects(self.client.get("/checkout/"), "/cart/")


class CheckoutTests(Base):
    def test_checkout_requires_login(self):
        self.client.logout()
        self.add(self.biryani)
        self.assertRedirects(self.client.get("/checkout/"), "/login/?next=/checkout/")

    def test_cart_survives_login(self):
        self.client.logout()
        self.add(self.biryani)
        self.client.post(reverse("login"), {"username": "asha", "password": "pw12345678", "next": "/checkout/"})
        self.assertEqual(self.client.get("/checkout/").status_code, 200)

    def test_empty_cart_redirects(self):
        self.assertRedirects(self.client.get("/checkout/"), "/cart/")

    def test_cod_order(self):
        r = self.place_cod()
        order = Order.objects.get()
        self.assertRedirects(r, reverse("order_detail", args=[order.pk]))
        self.assertEqual((order.status, order.payment_method, order.payment_status), ("placed", "cod", "pending"))
        self.assertEqual(order.total, 600)
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.lines.get().quantity, 2)
        self.assertEqual(self.client.session["cart"], {})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.phone, "9876543210")

    def test_price_is_server_side(self):
        self.add(self.biryani)
        self.client.post(reverse("checkout"), {"name": "A", "phone": "9876543210", "address": "Somewhere 1",
                                               "payment_method": "cod", "total": "1"})
        self.assertEqual(Order.objects.get().total, 300)

    def test_validation_errors(self):
        self.add(self.biryani)
        for bad in [{"phone": "12"}, {"address": "x"}, {"name": "  "}, {"payment_method": "bitcoin"}]:
            data = {"name": "A", "phone": "9876543210", "address": "12 MG Road", "payment_method": "cod"}
            data.update(bad)
            r = self.client.post(reverse("checkout"), data)
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(Order.objects.count(), 0)

    def test_bad_coordinates_dropped(self):
        self.place_cod(lat="999", lng="5")
        o = Order.objects.get()
        self.assertIsNone(o.dest_lat)
        self.assertEqual(len(o.destination), 2)


class PaymentTests(Base):
    def make_online(self):
        self.place_cod(payment_method="online")
        return Order.objects.get()

    def test_online_order_waits_for_payment(self):
        o = self.make_online()
        self.assertEqual(o.status, "pending_payment")
        self.assertEqual(self.client.get(reverse("pay", args=[o.pk])).status_code, 200)
        self.client.login(username="boss", password="pw12345678")
        self.assertNotIn(o, list(self.client.get("/owner/").context["active"]))

    def test_successful_card_payment(self):
        o = self.make_online()
        r = self.client.post(reverse("pay", args=[o.pk]), {"mode": "card", "card_number": "4242 4242 4242 4242",
                                                           "expiry": "12/40", "cvv": "123"})
        self.assertRedirects(r, reverse("order_detail", args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual((o.status, o.payment_status), ("placed", "paid"))
        self.assertTrue(o.payment_ref.startswith("DEMO-"))
        self.assertEqual(self.client.get(reverse("pay", args=[o.pk])).status_code, 302)  # cannot pay twice

    def test_declined_invalid_and_expired(self):
        o = self.make_online()
        cases = [
            ({"mode": "card", "card_number": "4000000000000002", "expiry": "12/40", "cvv": "123"}, "declined"),
            ({"mode": "card", "card_number": "1234567812345678", "expiry": "12/40", "cvv": "123"}, "not valid"),
            ({"mode": "card", "card_number": "4242424242424242", "expiry": "01/20", "cvv": "123"}, "expired"),
            ({"mode": "card", "card_number": "4242424242424242", "expiry": "12/40", "cvv": "1"}, "CVV"),
            ({"mode": "upi", "upi": "bad"}, "UPI"),
            ({"mode": "x"}, "Choose"),
        ]
        for data, word in cases:
            r = self.client.post(reverse("pay", args=[o.pk]), data)
            self.assertEqual(r.status_code, 200)
            self.assertIn(word, r.context["error"])
        o.refresh_from_db()
        self.assertEqual((o.status, o.payment_status), ("pending_payment", "failed"))

    def test_retry_after_failure_and_upi(self):
        o = self.make_online()
        self.client.post(reverse("pay", args=[o.pk]), {"mode": "upi", "upi": "nope"})
        self.client.post(reverse("pay", args=[o.pk]), {"mode": "upi", "upi": "asha@okbank"})
        o.refresh_from_db()
        self.assertEqual((o.status, o.payment_status), ("placed", "paid"))

    def test_cannot_pay_someone_elses_order(self):
        o = self.make_online()
        User.objects.create_user("eve", password="pw12345678")
        self.client.login(username="eve", password="pw12345678")
        self.assertEqual(self.client.get(reverse("pay", args=[o.pk])).status_code, 404)

    def test_luhn(self):
        self.assertTrue(payments.luhn_ok("4242424242424242"))
        self.assertFalse(payments.luhn_ok("4242424242424241"))


class OrderLifecycleTests(Base):
    def setUp(self):
        super().setUp()
        self.place_cod()
        self.order = Order.objects.get()

    def test_other_users_cannot_see_order(self):
        User.objects.create_user("eve", password="pw12345678")
        self.client.login(username="eve", password="pw12345678")
        for url in [f"/order/{self.order.pk}/", f"/order/{self.order.pk}/status/"]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"/order/{self.order.pk}/cancel/").status_code, 404)
        self.assertEqual(self.client.post(f"/order/{self.order.pk}/reorder/").status_code, 404)

    def test_status_api_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(f"/order/{self.order.pk}/status/").status_code, 401)

    def test_customer_cancel_rules(self):
        self.order.status = "preparing"
        self.order.save()
        self.client.post(reverse("order_cancel", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")
        self.order.status = "placed"
        self.order.save()
        self.client.post(reverse("order_cancel", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")

    def test_paid_order_cancel_refunds(self):
        self.order.payment_status = "paid"
        self.order.save()
        self.client.post(reverse("order_cancel", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "refunded")

    def test_history_and_reorder(self):
        r = self.client.get("/orders/")
        self.assertContains(r, "Chicken Biryani")
        r = self.client.post(reverse("reorder", args=[self.order.pk]))
        self.assertRedirects(r, "/cart/")
        self.assertEqual(self.client.session["cart"], {str(self.biryani.pk): 2})

    def test_reorder_skips_unavailable(self):
        self.biryani.available = False
        self.biryani.save()
        r = self.client.post(reverse("reorder", args=[self.order.pk]))
        self.assertRedirects(r, "/orders/")

    @override_settings(DEMO_SECONDS_PER_KM=40)
    def test_tracking_moves_rider(self):
        self.order.dest_lat, self.order.dest_lng = 12.96, 77.65
        self.order.status = "on_the_way"
        self.order.dispatched_at = timezone.now()
        self.order.save()
        d = self.client.get(f"/order/{self.order.pk}/status/").json()
        self.assertEqual(d["status"], "on_the_way")
        self.assertLess(d["progress"], 0.1)
        self.order.dispatched_at = timezone.now() - timedelta(seconds=self.order.delivery_seconds // 2)
        self.order.save()
        d = self.client.get(f"/order/{self.order.pk}/status/").json()
        self.assertAlmostEqual(d["progress"], 0.5, delta=0.05)
        self.assertGreater(d["rider"]["lat"], self.r1.latitude)
        self.assertLess(d["rider"]["lat"], 12.96)
        self.order.dispatched_at = timezone.now() - timedelta(days=1)
        self.order.save()
        d = self.client.get(f"/order/{self.order.pk}/status/").json()
        self.assertEqual(d["progress"], 1.0)
        self.assertIn("less than a minute", d["eta_text"])
        self.assertEqual(self.client.get(f"/order/{self.order.pk}/").status_code, 200)


class OwnerTests(Base):
    def setUp(self):
        super().setUp()
        self.place_cod()
        self.order = Order.objects.get()
        self.client.login(username="boss", password="pw12345678")

    def test_customers_cannot_open_dashboard(self):
        self.client.login(username="asha", password="pw12345678")
        for url in ["/owner/", "/owner/menu/"]:
            self.assertRedirects(self.client.get(url), "/")
        self.assertEqual(self.client.post(f"/owner/order/{self.order.pk}/advance/", {"from": "placed"}).status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "placed")

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/owner/"), "/login/?next=/owner/")

    def test_dashboard_shows_order_and_stats(self):
        r = self.client.get("/owner/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["stats"]["orders"], 1)
        self.assertEqual(r.context["stats"]["revenue"], 600)
        self.assertEqual(len(r.context["active"]), 1)

    def test_full_status_flow_and_cod_paid_on_delivery(self):
        for expected in ["placed", "preparing", "on_the_way"]:
            self.client.post(f"/owner/order/{self.order.pk}/advance/", {"from": expected})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertIsNotNone(self.order.dispatched_at)

    def test_double_click_does_not_skip_a_step(self):
        self.client.post(f"/owner/order/{self.order.pk}/advance/", {"from": "placed"})
        self.client.post(f"/owner/order/{self.order.pk}/advance/", {"from": "placed"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")

    def test_owner_cancel_only_before_dispatch(self):
        self.client.post(f"/owner/order/{self.order.pk}/cancel/")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.order.status = "on_the_way"
        self.order.save()
        self.client.post(f"/owner/order/{self.order.pk}/cancel/")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "on_the_way")

    def test_owner_cannot_touch_other_restaurant(self):
        other = User.objects.create_user("rival", password="pw12345678")
        Restaurant.objects.create(name="Rival", latitude=1, longitude=1, owner=other)
        self.client.login(username="rival", password="pw12345678")
        self.assertEqual(self.client.post(f"/owner/order/{self.order.pk}/advance/", {"from": "placed"}).status_code, 404)
        self.assertEqual(self.client.post(f"/owner/menu/{self.biryani.pk}/delete/").status_code, 404)
        self.assertEqual(self.client.get(f"/owner/menu/{self.biryani.pk}/edit/").status_code, 404)

    def test_owner_can_view_own_order_page(self):
        self.assertEqual(self.client.get(f"/order/{self.order.pk}/").status_code, 200)

    def test_menu_crud(self):
        r = self.client.post("/owner/menu/", {"name": "Lassi", "description": "Sweet", "price": "60", "is_veg": "on", "available": "on"})
        self.assertRedirects(r, "/owner/menu/")
        lassi = MenuItem.objects.get(name="Lassi")
        self.assertEqual(lassi.restaurant, self.r1)
        self.client.post(f"/owner/menu/{lassi.pk}/edit/", {"name": "Mango Lassi", "price": "70", "is_veg": "on", "available": "on"})
        lassi.refresh_from_db()
        self.assertEqual(lassi.name, "Mango Lassi")
        self.client.post(f"/owner/menu/{lassi.pk}/toggle/")
        lassi.refresh_from_db()
        self.assertFalse(lassi.available)
        self.client.post(f"/owner/menu/{lassi.pk}/delete/")
        self.assertFalse(MenuItem.objects.filter(pk=lassi.pk).exists())

    def test_deleting_ordered_dish_hides_it_instead_of_crashing(self):
        r = self.client.post(f"/owner/menu/{self.biryani.pk}/delete/")
        self.assertRedirects(r, "/owner/menu/")
        self.biryani.refresh_from_db()
        self.assertFalse(self.biryani.available)

    def test_menu_validation(self):
        for bad in [{"price": "0"}, {"price": "-5"}, {"name": "   "}]:
            data = {"name": "X", "price": "10"}
            data.update(bad)
            r = self.client.post("/owner/menu/", data)
            self.assertEqual(r.status_code, 200, bad)
        self.assertFalse(MenuItem.objects.filter(name="X").exists())

    def test_toggle_open_blocks_ordering(self):
        self.client.post("/owner/toggle-open/")
        self.r1.refresh_from_db()
        self.assertFalse(self.r1.is_open)
        self.client.login(username="asha", password="pw12345678")
        self.add(self.coffee)
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_restaurant_setup_for_new_owner(self):
        self.client.post(reverse("signup") if False else "/logout/")
        self.client.post("/signup/", {"username": "chef", "password1": "Str0ng!pass99", "password2": "Str0ng!pass99", "is_owner": "on"})
        r = self.client.post("/owner/restaurant/", {"name": "Chef's Place", "cuisine": "Thai", "address": "Somewhere",
                                                    "latitude": "12.9", "longitude": "77.6", "is_open": "on"})
        self.assertRedirects(r, "/owner/")
        self.assertEqual(Restaurant.objects.get(name="Chef's Place").owner.username, "chef")
        bad = self.client.post("/owner/restaurant/", {"name": "Z", "latitude": "200", "longitude": "77"})
        self.assertEqual(bad.status_code, 200)

    def test_owner_ai_endpoints_without_key(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            r = self.client.post("/owner/api/describe/", json.dumps({"name": "Lassi", "is_veg": True}), content_type="application/json")
            self.assertIn("Lassi", r.json()["text"])
            self.assertEqual(self.client.post("/owner/api/describe/", json.dumps({"name": ""}), content_type="application/json").status_code, 400)
            self.assertEqual(self.client.post("/owner/api/describe/", "not json", content_type="application/json").status_code, 400)
            r = self.client.post("/owner/api/insights/")
            self.assertEqual(r.status_code, 200)
            self.order.advance(); self.order.advance(); self.order.advance()
            r = self.client.post("/owner/api/insights/")
            self.assertIn("Chicken Biryani", r.json()["text"])
        self.client.login(username="asha", password="pw12345678")
        self.assertEqual(self.client.post("/owner/api/insights/").status_code, 302)


class AITests(Base):
    def post(self, message, **extra):
        body = {"message": message}
        body.update(extra)
        return self.client.post("/api/assistant/", json.dumps(body), content_type="application/json")

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fallback_filters_veg_budget_and_nonveg(self):
        d = self.post("veg under 200").json()
        self.assertEqual(d["mode"], "basic")
        self.assertTrue(d["items"])
        self.assertTrue(all(i["is_veg"] and float(i["price"]) <= 200 for i in d["items"]))
        d = self.post("spicy non-veg").json()
        self.assertEqual([i["name"] for i in d["items"]], ["Chicken Biryani"])
        d = self.post("veg under 10").json()
        self.assertEqual(d["items"], [])

    def test_fallback_ignores_unavailable_and_closed(self):
        self.biryani.available = False
        self.biryani.save()
        self.assertEqual(self.post("chicken biryani").json()["items"], [])
        self.r2.is_open = False
        self.r2.save()
        self.assertEqual(self.post("pizza").json()["items"], [])

    def test_input_validation(self):
        self.assertEqual(self.client.post("/api/assistant/", "garbage", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/assistant/", "[1]", content_type="application/json").status_code, 400)
        self.assertEqual(self.post("   ").json()["items"], [])
        self.assertEqual(self.post("pizza", lat="abc", history="oops").status_code, 200)

    def test_throttle(self):
        codes = [self.post("pizza").status_code for _ in range(14)]
        self.assertEqual(codes[:12], [200] * 12)
        self.assertEqual(codes[12], 429)

    def test_ai_json_reply_is_validated(self):
        fake = json.dumps({"reply": "Try the biryani!", "item_ids": [self.biryani.pk, 99999, "x", self.biryani.pk]})
        with mock.patch.object(ai, "_call", return_value="Sure! " + fake + " enjoy"):
            d = self.post("something spicy").json()
        self.assertEqual(d["mode"], "ai")
        self.assertEqual(d["reply"], "Try the biryani!")
        self.assertEqual([i["id"] for i in d["items"]], [self.biryani.pk])

    def test_ai_plain_text_and_failure(self):
        with mock.patch.object(ai, "_call", return_value="Just eat something nice."):
            d = self.post("hello").json()
        self.assertEqual((d["reply"], d["items"], d["mode"]), ("Just eat something nice.", [], "ai"))
        with mock.patch.object(ai, "_call", return_value=None):
            self.assertEqual(self.post("chicken").json()["mode"], "basic")

    def test_prompt_contains_favourites_and_only_open_menu(self):
        self.place_cod()
        captured = {}

        def fake(system, messages, max_tokens=500):
            captured["system"], captured["messages"] = system, messages
            return None
        self.r2.is_open = False
        self.r2.save()
        with mock.patch.object(ai, "_call", side_effect=fake):
            self.post("hi", history=[{"role": "assistant", "content": "hey"}, {"role": "user", "content": "yo"}])
        self.assertIn("Chicken Biryani", captured["system"])
        self.assertIn("ordered before", captured["system"])
        self.assertNotIn("Margherita", captured["system"])
        self.assertEqual(captured["messages"][0]["role"], "user")
        self.assertEqual(captured["messages"][-1]["role"], "user")
        self.assertTrue(captured["messages"][-1]["content"].endswith("hi"))

    def test_history_cleaning(self):
        m = ai._clean_history([{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"},
                               {"role": "user", "content": "c"}, {"role": "system", "content": "evil"}, 5], "now")
        self.assertEqual([x["role"] for x in m], ["user"])
        self.assertIn("now", m[0]["content"])

    def test_cart_suggestions(self):
        self.assertEqual(self.client.get("/api/cart-suggest/").json(), {"suggestions": []})
        self.add(self.biryani)
        d = self.client.get("/api/cart-suggest/").json()["suggestions"]
        self.assertEqual({s["name"] for s in d}, {"Paneer Tikka", "Filter Coffee"})
        self.assertEqual(d[0]["name"], "Filter Coffee")  # cheapest first in fallback
        fake = json.dumps({"suggestions": [{"id": self.paneer.pk, "reason": "Great starter"}, {"id": self.pizza.pk, "reason": "other restaurant"}, {"id": self.biryani.pk, "reason": "in cart"}]})
        self.add(self.coffee)  # new cart signature so cache is bypassed
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", return_value=fake):
            d = self.client.get("/api/cart-suggest/").json()["suggestions"]
        self.assertEqual([(s["name"], s["reason"]) for s in d], [("Paneer Tikka", "Great starter")])

    def test_dish_description_fallback_and_ai(self):
        self.assertIn("Lassi", ai.describe_dish("Lassi", True, "X", "Indian"))
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", return_value='"Creamy and cool."'):
            self.assertEqual(ai.describe_dish("Lassi", True, "X", "Indian"), "Creamy and cool.")

    def test_real_call_failure_is_swallowed(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "bad-key"}), \
                mock.patch("anthropic.Anthropic", side_effect=RuntimeError("boom")):
            self.assertIsNone(ai._call("s", [{"role": "user", "content": "x"}]))
