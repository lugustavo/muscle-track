from django import template
from django.urls import reverse

from core.breadcrumbs import build_breadcrumbs
from core.navigation import back_label, safe_next_url, with_next

register = template.Library()


@register.simple_tag(takes_context=True)
def nav_url(context, view_name, *args, **kwargs):
    """URL of ``view_name`` that remembers the current page as ``?next=``."""
    url = reverse(view_name, args=args or None, kwargs=kwargs or None)
    return with_next(url, context['request'].get_full_path())


@register.simple_tag(takes_context=True)
def back_href(context, default_url):
    """Where "Back" leads on this page: a valid ``?next=`` or ``default_url``."""
    return safe_next_url(context['request'], default_url)


@register.inclusion_tag('core/_back_button.html', takes_context=True)
def back_button(context, default_url, label=None, css='btn btn-secondary', icon='bi-arrow-left', fragment=''):
    """A Back/Cancel button; the label is derived from the destination unless given."""
    url = safe_next_url(context['request'], default_url) + fragment
    return {'url': url, 'label': label or back_label(url), 'css': css, 'icon': icon}


@register.inclusion_tag('core/_breadcrumbs.html', takes_context=True)
def breadcrumbs(context):
    """Breadcrumb trail of the current page (see core/breadcrumbs.py)."""
    request = context.get('request')
    return {'trail': build_breadcrumbs(request, context) if request is not None else []}
