"""
Template tags designed to work with applications which use comment
moderation.

"""

from django import template
from comment_utils.moderation import moderator

def comments_open(value):
    """
    Return ``True`` if new comments are allowed for an object,
    ``False`` otherwise.
    
    """
    return moderator.comments_open(value)

def comments_moderated(value):
    """
    Return ``True`` if new comments for an object are being
    automatically sent into moderation, ``False`` otherwise.
    
    """
    return moderator.comments_moderated(value)


register = template.Library()
register.filter(comments_open)
register.filter(comments_moderated)
