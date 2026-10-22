"""Assessment photos open inside the app (a viewer), not in a new browser tab; and two small layout fixes next to it."""
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ProgressPhoto

from .base import BaseTestCase
from .test_evolution import MEDIA, picture
from .test_navigation_global import RichDataMixin


def static_text(name):
    from django.conf import settings
    return (settings.BASE_DIR / 'static' / name).read_text()


@override_settings(MEDIA_ROOT=MEDIA)
class PhotoViewerTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build_world()
        self.photo = ProgressPhoto.objects.create(person=self.person, angle='front', image=picture(), assessment=self.assessment)
        self.loose = ProgressPhoto.objects.create(person=self.person, angle='back', image=picture(), uploaded_by_student=True)

    def photo_links(self, page):
        import re
        return re.findall(r'<a [^>]*href="[^"]*/photos/\d+/file/?[^"]*"[^>]*>', page)

    def test_the_trainers_file_opens_photos_in_the_viewer(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        links = self.photo_links(page)
        self.assertGreaterEqual(len(links), 2)
        for link in links:
            self.assertIn('data-photo-view', link)
            self.assertNotIn('target="_blank"', link)

    def test_the_students_own_page_opens_photos_in_the_viewer(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:my_evolution')).content.decode()
        links = self.photo_links(page)
        self.assertGreaterEqual(len(links), 2)
        for link in links:
            self.assertIn('data-photo-view', link)
            self.assertNotIn('target="_blank"', link)

    def test_the_compare_page_opens_photos_in_the_viewer(self):
        self.login(self.trainer)
        other = ProgressPhoto.objects.create(person=self.person, angle='front', image=picture(), date=timezone.localdate())
        page = self.client.get(reverse('core:student_compare', kwargs={'pk': self.person.pk}) +
                               f'?angle=front&before=photo:{self.photo.pk}&after=photo:{other.pk}').content.decode()
        for link in self.photo_links(page):
            self.assertIn('data-photo-view', link)
            self.assertNotIn('target="_blank"', link)

    def test_the_viewer_script_is_loaded_on_every_page_and_walks_the_gallery(self):
        self.login(self.trainer)
        self.assertIn('js/photo-viewer.js', self.client.get(reverse('core:home')).content.decode())
        script = static_text('js/photo-viewer.js')
        for needle in ("a[data-photo-view]", 'ArrowLeft', 'touchend', 'modal-fullscreen-sm-down', "data-photo-prev"):
            self.assertIn(needle, script)
        self.assertNotIn('_blank', script)

    def test_the_progress_page_header_stacks_its_buttons_instead_of_overlapping(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:my_evolution')).content.decode()
        self.assertIn('<div class="ui-head">', page)               # not the one-line "inline" header
        self.assertIn('<div class="ui-actions fill">', page)

    def test_the_notice_centre_header_stacks_too(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:notices')).content.decode()
        self.assertIn('<div class="ui-head">', page)

    def test_todays_classes_card_has_a_button_in_the_apps_style(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:home')).content.decode()
        card = page.split('id="card-classes"', 1)[1].split('list-group list-group-flush', 1)[0]
        self.assertIn('class="btn btn-sm btn-outline-primary" data-open-agenda', card)
        self.assertIn('data-classes-count', card)
        self.assertNotIn('class="small"', card)
