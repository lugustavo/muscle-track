"""Shared bits of the list pages (exercises, workouts, packages, students): the view mode a person picked is kept in a
cookie, so the list opens the same way next time, and the active filters become removable tags."""
VIEW_MODES = ('grid', 'list')
COOKIE_MAX_AGE = 31536000   # one year


def view_mode(request, cookie, allowed=VIEW_MODES, default='grid'):
    """The layout to show: `?view=` first, then the cookie, then the default; anything not allowed falls back."""
    for value in (request.GET.get('view'), request.COOKIES.get(cookie)):
        if value in allowed:
            return value
    return default


def remember_view(request, response, cookie, mode):
    """Keep the chosen layout when it came from the address (`?view=`)."""
    if request.GET.get('view') == mode:
        response.set_cookie(cookie, mode, max_age=COOKIE_MAX_AGE, samesite='Lax')
    return response


def filter_tags(search='', **chosen):
    """Tags for the active filters: `[{'key', 'label'}]`; a filter without a label is not active."""
    tags = [{'key': 'search', 'label': f'“{search}”'}] if search else []
    tags += [{'key': key, 'label': label} for key, label in chosen.items() if label]
    return tags
