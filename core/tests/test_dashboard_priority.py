import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.dashboard import PRIORITY, priorities, trainer_dashboard
from core.models import Appointment, Person, StudentEvent

from .base import BaseTestCase


class PriorityOrderTests(BaseTestCase):
    def test_nothing_to_do_means_no_chips_and_every_card_sinks_in_order(self):
        chips, order = priorities({})
        self.assertEqual(chips, [])
        self.assertEqual(sorted(order, key=order.get)[:len(PRIORITY)], [key for key, _label, _level in PRIORITY])
        self.assertTrue(all(value >= 40 for value in order.values()))

    def test_cards_with_work_come_first_and_most_pressing_leads(self):
        chips, order = priorities({'inactive': 3, 'erasures': 1, 'payments': 2, 'classes': 0, 'sessions': 4})
        self.assertEqual([c['key'] for c in chips], ['erasures', 'payments', 'inactive'])   # PRIORITY order, empties left out
        self.assertEqual([c['count'] for c in chips], [1, 2, 3])
        ranked = sorted(order, key=order.get)
        self.assertEqual(ranked[:3], ['erasures', 'payments', 'inactive'])
        self.assertLess(order['inactive'], order['classes'])      # a card with work beats an empty one, even a more pressing one
        self.assertLess(order['sessions'], order['comments'])
        self.assertLess(order['inactive'], order['sessions'])     # information comes after what needs doing
        self.assertNotIn('sessions', [c['key'] for c in chips])   # never "to do"

    def test_every_card_has_an_order_and_a_chip_key_has_a_card(self):
        data = trainer_dashboard()
        self.assertEqual(set(data['order']), {key for key, _l, _v in PRIORITY} | {'sessions', 'comments', 'birthdays'})
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:home')).content.decode()
        always_there = ('inactive', 'classes', 'plans', 'routines', 'sessions', 'comments')   # the others appear only with something to list
        for key in always_there:
            self.assertRegex(page, rf'id="card-{key}" style="order: {data["order"][key]};"')


class DashboardPageTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.trainer)

    def page(self):
        return self.client.get(reverse('core:home')).content.decode()

    def test_a_quiet_day_says_so(self):
        page = self.page()
        self.assertIn('data-attention', page)
        self.assertIn('data-all-clear', page)   # fixtures: nobody late, no class today (Alice and Bob never trained, but are new)

    def test_the_strip_lists_what_needs_attention_and_links_to_the_cards(self):
        old = timezone.now() - timedelta(days=20)
        Person.objects.filter(pk=self.alice.person.pk).update(created_at=old)
        StudentEvent.objects.create(person=self.bob.person, kind=StudentEvent.Kind.CONSENT, summary='x')   # noise: not a card
        Appointment.objects.create(person=self.alice.person, starts_at=timezone.now() + timedelta(minutes=5))
        page = self.page()
        self.assertNotIn('data-all-clear', page)
        chips = re.findall(r'data-attention-chip="(\w+)"', page)
        self.assertIn('classes', chips)
        for key in chips:
            self.assertIn(f'id="card-{key}"', page)       # every chip points at a card that is on the page
            self.assertIn(f'href="#card-{key}"', page)

    def test_the_student_home_has_no_strip(self):
        self.client.force_login(self.alice)
        self.assertNotIn('data-attention', self.client.get(reverse('core:home')).content.decode())
