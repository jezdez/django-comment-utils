"""
Template tags designed to work with applications which use comment
moderation.

"""


from django import template
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import get_model
from django.contrib.contenttypes.models import ContentType

from threadedcomments.models import ThreadedComment as Comment
from threadedcomments.models import FreeThreadedComment as FreeComment
from comment_utils.moderation import moderator


class CommentCountNode(template.Node):
    def __init__(self, package, module, context_var_name, obj_id, var_name, free):
        self.package, self.module = package, module
        if context_var_name is not None:
            context_var_name = template.Variable(context_var_name)
        self.context_var_name, self.obj_id = context_var_name, obj_id
        self.var_name, self.free = var_name, free

    def render(self, context):
        from django.conf import settings
        manager = self.free and FreeComment.objects or Comment.objects
        if self.context_var_name is not None:
            self.obj_id = self.context_var_name.resolve(context)
        comment_count = manager.filter(object_id__exact=self.obj_id,
            content_type__app_label__exact=self.package,
            content_type__model__exact=self.module, site__id__exact=settings.SITE_ID).count()
        context[self.var_name] = comment_count
        return ''

class PublicCommentCountNode(CommentCountNode):
    def render(self, context):
        from django.conf import settings
        manager = self.free and FreeComment.objects or Comment.objects
        if self.context_var_name is not None:
            object_id = self.context_var_name.resolve(context)
        comment_count = manager.filter(object_id__exact=object_id,
                                       content_type__app_label__exact=self.package,
                                       content_type__model__exact=self.module,
                                       site__id__exact=settings.SITE_ID,
                                       is_public__exact=True).count()
        context[self.var_name] = comment_count
        return ''

class DoGetCommentList:
    """
    Gets comments for the given params and populates the template context with a
    special comment_package variable, whose name is defined by the ``as``
    clause.

    Syntax::

        {% get_comment_list for [pkg].[py_module_name] [context_var_containing_obj_id] as [varname] (reversed) %}

    Example usage::

        {% get_comment_list for lcom.eventtimes event.id as comment_list %}

    Note: ``[context_var_containing_obj_id]`` can also be a hard-coded integer, like this::

        {% get_comment_list for lcom.eventtimes 23 as comment_list %}

    To get a list of comments in reverse order -- that is, most recent first --
    pass ``reversed`` as the last param::

        {% get_comment_list for lcom.eventtimes event.id as comment_list reversed %}
    """
    def __init__(self, free):
        self.free = free

    def __call__(self, parser, token):
        tokens = token.contents.split()
        # Now tokens is a list like this:
        # ['get_comment_list', 'for', 'lcom.eventtimes', 'event.id', 'as', 'comment_list']
        if not len(tokens) in (6, 7):
            raise template.TemplateSyntaxError, "%r tag requires 5 or 6 arguments" % tokens[0]
        if tokens[1] != 'for':
            raise template.TemplateSyntaxError, "Second argument in %r tag must be 'for'" % tokens[0]
        try:
            package, module = tokens[2].split('.')
        except ValueError: # unpack list of wrong size
            raise template.TemplateSyntaxError, "Third argument in %r tag must be in the format 'package.module'" % tokens[0]
        try:
            content_type = ContentType.objects.get(app_label__exact=package,model__exact=module)
        except ContentType.DoesNotExist:
            raise template.TemplateSyntaxError, "%r tag has invalid content-type '%s.%s'" % (tokens[0], package, module)
        var_name, obj_id = None, None
        if tokens[3].isdigit():
            obj_id = tokens[3]
            try: # ensure the object ID is valid
                content_type.get_object_for_this_type(pk=obj_id)
            except ObjectDoesNotExist:
                raise template.TemplateSyntaxError, "%r tag refers to %s object with ID %s, which doesn't exist" % (tokens[0], content_type.name, obj_id)
        else:
            var_name = tokens[3]
        if tokens[4] != 'as':
            raise template.TemplateSyntaxError, "Fourth argument in %r must be 'as'" % tokens[0]
        if len(tokens) == 7:
            if tokens[6] != 'reversed':
                raise template.TemplateSyntaxError, "Final argument in %r must be 'reversed' if given" % tokens[0]
            ordering = "-"
        else:
            ordering = ""
        return CommentListNode(package, module, var_name, obj_id, tokens[5], self.free, ordering)

class CommentListNode(template.Node):
    def __init__(self, package, module, context_var_name, obj_id, var_name, free, ordering, extra_kwargs=None):
        self.package, self.module = package, module
        if context_var_name is not None:
            context_var_name = template.Variable(context_var_name)
        self.context_var_name, self.obj_id = context_var_name, obj_id
        self.var_name, self.free = var_name, free
        self.ordering = ordering
        self.extra_kwargs = extra_kwargs or {}

    def render(self, context):
        from django.conf import settings
        get_list_function = self.free and FreeComment.objects.filter or Comment.objects.get_list_with_karma
        if self.context_var_name is not None:
            try:
                self.obj_id = self.context_var_name.resolve(context)
            except template.VariableDoesNotExist:
                return ''
        kwargs = {
            'object_id__exact': self.obj_id,
            'content_type__app_label__exact': self.package,
            'content_type__model__exact': self.module,
            'site__id__exact': settings.SITE_ID,
        }
        kwargs.update(self.extra_kwargs)
        comment_list = get_list_function(**kwargs).order_by(self.ordering + 'submit_date').select_related()
        if not self.free and settings.COMMENTS_BANNED_USERS_GROUP:
            comment_list = comment_list.extra(select={'is_hidden': 'user_id IN (SELECT user_id FROM auth_user_groups WHERE group_id = %s)' % settings.COMMENTS_BANNED_USERS_GROUP})

        if not self.free:
            if 'user' in context and context['user'].is_authenticated():
                user_id = context['user'].id
                context['user_can_moderate_comments'] = Comment.objects.user_is_moderator(context['user'])
            else:
                user_id = None
                context['user_can_moderate_comments'] = False
            # Only display comments by banned users to those users themselves.
            if settings.COMMENTS_BANNED_USERS_GROUP:
                comment_list = [c for c in comment_list if not c.is_hidden or (user_id == c.user_id)]

        context[self.var_name] = comment_list
        return ''


class DoPublicCommentList(DoGetCommentList):
    """
    Retrieves comments for a particular object and stores them in a
    context variable.

    The difference between this tag and Django's built-in comment list
    tags is that this tag will only return comments with
    ``is_public=True``. If your application uses any sort of comment
    moderation which sets ``is_public=False``, you'll probably want to
    use this tag, as it makes the template logic simpler by only
    returning approved comments.
    
    Syntax::
    
        {% get_public_comment_list for [app_name].[model_name] [object_id] as [varname] %}
    
    or::
    
        {% get_public_free_comment_list for [app_name].[model_name] [object_id] as [varname] %}
    
    When called as ``get_public_comment_list``, this tag retrieves
    instances of ``Comment`` (comments which require
    registration). When called as ``get_public_free_comment_list``,
    this tag retrieves instances of ``FreeComment`` (comments which do
    not require registration).
    
    To retrieve comments in reverse order (e.g., newest comments
    first), pass 'reversed' as an extra argument after ``varname``.
    
    So, for example, to retrieve registered comments for a flatpage
    with ``id`` 12, use like this::
    
        {% get_public_comment_list for flatpages.flatpage 12 as comment_list %}
    
    To retrieve unregistered comments for the same object::
    
        {% get_public_free_comment_list for flatpages.flatpage 12 as comment_list %}
    
    To retrieve in reverse order (newest comments first)::
    
        {% get_public_free_comment_list for flatpages.flatpage 12 as comment_list reversed %}
        
    """
    def __call__(self, parser, token):
        bits = token.contents.split()
        if len(bits) not in (6, 7):
            raise template.TemplateSyntaxError("'%s' tag takes 5 or 6 arguments" % bits[0])
        if bits[1] != 'for':
            raise template.TemplateSyntaxError("first argument to '%s' tag must be 'for'" % bits[0])
        try:
            app_name, model_name = bits[2].split('.')
        except ValueError:
            raise template.TemplateSyntaxError("second argument to '%s' tag must be in the form 'app_name.model_name'" % bits[0])
        model = get_model(app_name, model_name)
        if model is None:
            raise template.TemplateSyntaxError("'%s' tag got invalid model '%s.%s'" % (bits[0], app_name, model_name))
        content_type = ContentType.objects.get_for_model(model)
        var_name, object_id = None, None
        if bits[3].isdigit():
            object_id = bits[3]
            try:
                content_type.get_object_for_this_type(pk=object_id)
            except ObjectDoesNotExist:
                raise template.TemplateSyntaxError("'%s' tag got reference to %s object with id %s, which doesn't exist" % (bits[0], content_type.name, object_id))
        else:
            var_name = bits[3]
        if bits[4] != 'as':
            raise template.TemplateSyntaxError("fourth argument to '%s' tag must be 'as'" % bits[0])
        if len(bits) == 7:
            if bits[6] != 'reversed':
                raise template.TemplateSyntaxError("sixth argument to '%s' tag, if given, must be 'reversed'" % bits[0])
            ordering = '-'
        else:
            ordering = ''
        return CommentListNode(app_name, model_name, var_name, object_id, bits[5], self.free, ordering, extra_kwargs={ 'is_public__exact': True })

class DoCommentCount:
    """
    Gets comment count for the given params and populates the template context
    with a variable containing that value, whose name is defined by the 'as'
    clause.

    Syntax::

        {% get_comment_count for [pkg].[py_module_name] [context_var_containing_obj_id] as [varname]  %}

    Example usage::

        {% get_comment_count for lcom.eventtimes event.id as comment_count %}

    Note: ``[context_var_containing_obj_id]`` can also be a hard-coded integer, like this::

        {% get_comment_count for lcom.eventtimes 23 as comment_count %}
    """
    def __init__(self, free):
        self.free = free

    def __call__(self, parser, token):
        tokens = token.contents.split()
        # Now tokens is a list like this:
        # ['get_comment_list', 'for', 'lcom.eventtimes', 'event.id', 'as', 'comment_list']
        if len(tokens) != 6:
            raise template.TemplateSyntaxError, "%r tag requires 5 arguments" % tokens[0]
        if tokens[1] != 'for':
            raise template.TemplateSyntaxError, "Second argument in %r tag must be 'for'" % tokens[0]
        try:
            package, module = tokens[2].split('.')
        except ValueError: # unpack list of wrong size
            raise template.TemplateSyntaxError, "Third argument in %r tag must be in the format 'package.module'" % tokens[0]
        try:
            content_type = ContentType.objects.get(app_label__exact=package, model__exact=module)
        except ContentType.DoesNotExist:
            raise template.TemplateSyntaxError, "%r tag has invalid content-type '%s.%s'" % (tokens[0], package, module)
        var_name, obj_id = None, None
        if tokens[3].isdigit():
            obj_id = tokens[3]
            try: # ensure the object ID is valid
                content_type.get_object_for_this_type(pk=obj_id)
            except ObjectDoesNotExist:
                raise template.TemplateSyntaxError, "%r tag refers to %s object with ID %s, which doesn't exist" % (tokens[0], content_type.name, obj_id)
        else:
            var_name = tokens[3]
        if tokens[4] != 'as':
            raise template.TemplateSyntaxError, "Fourth argument in %r must be 'as'" % tokens[0]
        return CommentCountNode(package, module, var_name, obj_id, tokens[5], self.free)


class DoPublicCommentCount(DoCommentCount):
    """
    Retrieves the number of comments attached to a particular object
    and stores them in a context variable.

    The difference between this tag and Django's built-in comment
    count tags is that this tag will only count comments with
    ``is_public=True``. If your application uses any sort of comment
    moderation which sets ``is_public=False``, you'll probably want to
    use this tag, as it gives an accurate count of the comments which
    will be publicly displayed.
    
    Syntax::
    
        {% get_public_comment_count for [app_name].[model_name] [object_id] as [varname] %}
    
    or::
    
        {% get_public_free_comment_count for [app_name].[model_name] [object_id] as [varname] %}
    
    Example::

        {% get_public_comment_count for weblog.entry entry.id as comment_count %}

    When called as ``get_public_comment_list``, this tag counts
    instances of ``Comment`` (comments which require
    registration). When called as ``get_public_free_comment_count``,
    this tag counts instances of ``FreeComment`` (comments which do
    not require registration).

    """
    def __call__(self, parser, token):
        bits = token.contents.split()
        if len(bits) != 6:
            raise template.TemplateSyntaxError("'%s' tag takes five arguments" % bits[0])
        if bits[1] != 'for':
            raise template.TemplateSyntaxError("first argument to '%s' tag must be 'for'" % bits[0])
        try:
            app_name, model_name = bits[2].split('.')
        except ValueError:
            raise template.TemplateSyntaxError("second argument to '%s tag must be in the format app_name.model_name'" % bits[0])
        model = get_model(app_name, model_name)
        if model is None:
            raise template.TemplateSyntaxError("'%s' tag got invalid model '%s.%s'" % (bits[0], app_name, model_name))
        content_type = ContentType.objects.get_for_model(model)
        var_name, object_id = None, None
        if bits[3].isdigit():
            object_id = bits[3]
            try:
                content_type.get_object_for_this_type(pk=object_id)
            except ObjectDoesNotExist:
                raise template.TemplateSyntaxError("'%s' tag got reference to %s object with id %s, which doesn't exist" % (bits[0], content_type.name, object_id))
        else:
            var_name = bits[3]
        if bits[4] != 'as':
            raise template.TemplateSyntaxError("fourth argument to '%s' tag must be 'as'" % bits[0])
        
        return PublicCommentCountNode(app_name, model_name, var_name, object_id, bits[5], self.free)

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
register.tag('get_public_comment_list', DoPublicCommentList(False))
register.tag('get_public_free_comment_list', DoPublicCommentList(True))
register.tag('get_public_comment_count', DoPublicCommentCount(False))
register.tag('get_public_free_comment_count', DoPublicCommentCount(True))
register.filter(comments_open)
register.filter(comments_moderated)
