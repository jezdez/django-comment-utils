"""
A generic comment-moderation system which allows configuration of
moderation options on a per-model basis.

To use, do two things:

1. Create or import a subclass of ``CommentModerator`` defining the
   options you want.

2. Import ``moderator`` from this module and register one or more
   models, passing the models and the ``CommentModerator`` options
   class you want to use.


Example
-------

First, we define a simple model class which might represent entries in
a weblog::
    
    from django.db import models
    
    class Entry(models.Model):
        title = models.CharField(maxlength=250)
        body = models.TextField()
        pub_date = models.DateField()
        enable_comments = models.BooleanField()

Then we create a ``CommentModerator`` subclass specifying some
moderation options::
    
    from comment_utils.moderation import CommentModerator, moderator
    
    class EntryModerator(CommentModerator):
        akismet = True
        email_notification = True
        enable_field = 'enable_comments'

And finally register it for moderation::
    
    moderator.register(Entry, EntryModerator)

This sample class would apply several moderation steps to each new
comment submitted on an Entry:

* If the entry's ``enable_comments`` field is set to ``False``, the
  comment will be rejected (immediately deleted).

* If the comment is allowed to post, it will be submitted to an
  Akismet spam check (requires the Python Akismet module and an
  Akismet API key); if Akismet thinks the comment is spam, its
  ``is_public`` field will be set to ``False``.

* If the comment is successfully posted, an email notification of the
  comment will be sent to site staff.

For a full list of built-in moderation options and other
configurability, see the documentation for the ``CommentModerator``
class.

Several example subclasses of ``CommentModerator`` are provided in
this module as well, both to provide common moderation options and to
demonstrate some of the ways subclasses can customize moderation
behavior.

"""


import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import signals
from django.db.models.base import ModelBase
from django.template import Context, loader
from django.contrib import comments
from django.contrib.sites.models import Site

class AlreadyModerated(Exception):
    """
    Raised when a model which is already registered for moderation is
    attempting to be registered again.
    
    """
    pass


class NotModerated(Exception):
    """
    Raised when a model which is not registered for moderation is
    attempting to be unregistered.
    
    """
    pass


class CommentModerator(object):
    """
    Encapsulates comment-moderation options for a given model.
    
    This class is not designed to be used directly, since it doesn't
    enable any of the available moderation options. Instead, subclass
    it and override attributes to enable different options::
    
    ``akismet``
        If ``True``, comments will be submitted to an Akismet spam
        check and, if Akismet thinks they're spam, will have their
        ``is_public`` field set to ``False`` before saving. If
        this is enabled, you will need to have the Python Akismet
        module installed, and you will need to add the setting
        ``AKISMET_API_KEY`` to your Django settings file; the
        value of this setting should be a valid Akismet API
        key. Default value is ``False``.
    
    ``auto_close_field``
        If this is set to the name of a ``DateField`` or
        ``DateTimeField`` on the model for which comments are
        being moderated, new comments for objects of that model
        will be disallowed (immediately deleted) when a certain
        number of days have passed after the date specified in
        that field. Must be used in conjunction with
        ``close_after``, which specifies the number of days past
        which comments should be disallowed. Default value is
        ``None``.
    
    ``auto_moderate_field``
        Like ``auto_close_field``, but instead of outright
        deleting new comments when the requisite number of days
        have elapsed, it will simply set the ``is_public`` field
        of new comments to ``False`` before saving them. Must be
        used in conjunction with ``moderate_after``, which
        specifies the number of days past which comments should be
        moderated. Default value is ``None``.
    
    ``close_after``
        If ``auto_close_field`` is used, this must specify the
        number of days past the value of the field specified by
        ``auto_close_field`` after which new comments for an
        object should be disallowed. Default value is ``None``.
    
    ``email_notification``
        If ``True``, any new comment on an object of this model
        which survives moderation will generate an email to site
        staff. Default value is ``False``.
    
    ``enable_field``
        If this is set to the name of a ``BooleanField`` on the
        model for which comments are being moderated, new comments
        on objects of that model will be disallowed (immediately
        deleted) whenever the value of that field is ``False`` on
        the object the comment would be attached to. Default value
        is ``None``.
    
    ``moderate_after``
        If ``auto_moderate`` is used, this must specify the number
        of days past the value of the field specified by
        ``auto_moderate_field`` after which new comments for an
        object should be marked non-public. Default value is
        ``None``.
    
    Most common moderation needs can be covered by changing these
    attributes, but further customization can be obtained by
    subclassing and overriding the following methods. Each method will
    be called with two arguments: ``comment``, which is the comment
    being submitted, and ``content_object``, which is the object the
    comment will be attached to::
    
    ``allow``
        Should return ``True`` if the comment should be allowed to
        post on the content object, and ``False`` otherwise (in
        which case the comment will be immediately deleted).
    
    ``email``
        If email notification of the new comment should be sent to
        site staff or moderators, this method is responsible for
        sending the email.
    
    ``moderate``
        Should return ``True`` if the comment should be moderated
        (in which case its ``is_public`` field will be set to
        ``False`` before saving), and ``False`` otherwise (in
        which case the ``is_public`` field will not be changed).
    
    Subclasses which want to introspect the model for which comments
    are being moderated can do so through the attribute ``_model``,
    which will be the model class.
    
    """
    akismet = False
    auto_close_field = None
    auto_moderate_field = None
    close_after = None
    email_notification = False
    enable_field = None
    moderate_after = None
    moderate_field = None
    
    def __init__(self, model):
        self._model = model
    
    def _get_delta(self, now, then):
        """
        Internal helper which will return a ``datetime.timedelta``
        representing the time between ``now`` and ``then``. Assumes
        ``now`` is a ``datetime.date`` or ``datetime.datetime`` later
        than ``then``.
        
        If ``now`` and ``then`` are not of the same type due to one of
        them being a ``datetime.date`` and the other being a
        ``datetime.datetime``, both will be coerced to
        ``datetime.date`` before calculating the delta.
        
        """
        if now.__class__ is not then.__class__:
            now = datetime.date(now.year, now.month, now.day)
            then = datetime.date(then.year, then.month, then.day)
        if now < then:
            raise ValueError("Cannot determine moderation rules because date field is set to a value in the future")
        return now - then
        
    def allow(self, comment, content_object):
        """
        Determine whether a given comment is allowed to be posted on
        a given object.
        
        Return ``True`` if the comment should be allowed, ``False
        otherwise.
        
        """
        if self.enable_field:
            if not getattr(content_object, self.enable_field):
                return False
        if self.auto_close_field and self.close_after:
            if self._get_delta(datetime.datetime.now(), getattr(content_object, self.auto_close_field)).days >= self.close_after:
                return False
        return True
    
    def moderate(self, comment, content_object):
        """
        Determine whether a given comment on a given object should be
        allowed to show up immediately, or should be marked non-public
        and await approval.
        
        Return ``True`` if the comment should be moderated (marked
        non-public), ``False`` otherwise.
        
        """
        if self.auto_moderate_field and self.moderate_after:
            if self._get_delta(datetime.datetime.now(), getattr(content_object, self.auto_moderate_field)).days >= self.moderate_after:
                return True
        if self.akismet:
            from akismet import Akismet
            from django.utils.encoding import smart_str
            akismet_api = Akismet(key=settings.AKISMET_API_KEY,
                                  blog_url='http://%s/' % Site.objects.get_current().domain)
            if akismet_api.verify_key():
                akismet_data = { 'comment_type': 'comment',
                                 'referrer': '',
                                 'user_ip': comment.ip_address,
                                 'user_agent': '' }
                if akismet_api.comment_check(smart_str(comment.comment), data=akismet_data, build_data=True):
                    return True
        return False
    
    def comments_open(self, obj):
        """
        Return ``True`` if new comments are being accepted for
        ``obj``, ``False`` otherwise.
        
        The algorithm for determining this is as follows:
        
        1. If ``enable_field`` is set and the relevant field on
           ``obj`` contains a false value, comments are not open.
        
        2. If ``close_after`` is set and the relevant date field on
           ``obj`` is far enough in the past, comments are not open.
        
        3. If neither of the above checks determined that comments are
           not open, comments are open.
        
        """
        if self.enable_field:
            if not getattr(obj, self.enable_field):
                return False
        if self.auto_close_field and self.close_after:
            if self._get_delta(datetime.datetime.now(), getattr(obj, self.auto_close_field)).days >= self.close_after:
                return False
        return True
    
    def comments_moderated(self, obj):
        """
        Return ``True`` if new comments for ``obj`` are being
        automatically sent to moderation, ``False`` otherwise.
        
        The algorithm for determining this is as follows:
        
        1. If ``moderate_field`` is set and the relevant field on
           ``obj`` contains a true value, comments are moderated.
        
        2. If ``moderate_after`` is set and the relevant date field on
           ``obj`` is far enough in the past, comments are moderated.
        
        3. If neither of the above checks decided that comments are
           moderated, comments are not moderated.
        
        """
        if self.moderate_field:
            if getattr(obj, self.moderate_field):
                return True
        if self.auto_moderate_field and self.moderate_after:
            if self._get_delta(datetime.datetime.now(), getattr(obj, self.auto_moderate_field)).days >= self.moderate_after:
                return True
        return False
    
    def email(self, comment, content_object):
        """
        Send email notification of a new comment to site staff when email
        notifications have been requested.
        
        """
        if not self.email_notification:
            return
        recipient_list = [manager_tuple[1] for manager_tuple in settings.MANAGERS]
        t = loader.get_template('comment_utils/comment_notification_email.txt')
        c = Context({ 'comment': comment,
                      'content_object': content_object,
                      'site': Site.objects.get_current(),
                      })
        subject = '[%s] Comment: "%s"' % (Site.objects.get_current().name,
                                                          content_object)
        message = t.render(c)
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=True)


class AkismetModerator(CommentModerator):
    """
    Subclass of ``CommentModerator`` which applies Akismet spam
    filtering to all new comments for its model.
    
    """
    akismet = True


class AlwaysModerate(CommentModerator):
    """
    Subclass of ``CommentModerator`` which forces all new comments for
    its model into moderation (marks all comments non-public to begin
    with).
    
    """
    def moderate(self, comment, content_object):
        """
        Always return ``True``, no matter what comment or content
        object is supplied, so that new comments always get marked
        non-public to start with.
        
        """
        return True

    def comments_moderated(self, obj):
        """
        Always return ``True``, no matter what object is supplied,
        because new comments always get moderated.
        
        """
        return True


class NoComments(CommentModerator):
    """
    Subclass of ``CommentModerator`` which forbids all new comments
    for its model (deletes all comments posted to objects of that
    model).
    
    """
    def allow(self, comment, content_object):
        """
        Always return ``False`` because new comments are never allowed
        for this model.
        
        """
        return False

    def comments_open(self, obj):
        """
        Always return ``False``, because new comments are never
        allowed for this model.
        
        """
        return False
    

class ModerateFirstTimers(CommentModerator):
    """
    Subclass of ``CommentModerator`` which automatically moderates all
    comments from anyone who has not previously had a comment
    approved, while allowing all other comments to skip moderation.
    
    """
    def moderate(self, comment, content_object):
        """
        For each new comment, checks to see if the person submitting
        it has any previously-approved comments; if not, the comment
        will be moderated.
        
        """
        person_kwargs = { 'username__exact': comment.name }
        approved_comments = comment_class.objects.filter(is_public__exact=True, **person_kwargs)
        if approved_comments.count() == 0:
            return True
        return False


class Moderator(object):
    """
    Handles moderation of a set of models.
    
    An instance of this class will maintain a list of one or more
    models registered for comment moderation, and their associated
    moderation classes, and apply moderation to all incoming comments.
    
    To register a model, obtain an instance of ``CommentModerator``
    (this module exports one as ``moderator``), and call its
    ``register`` method, passing the model class and a moderation
    class (which should be a subclass of ``CommentModerator``). Note
    that both of these should be the actual classes, not instances of
    the classes.
    
    To cease moderation for a model, call the ``unregister`` method,
    passing the model class.
    
    For convenience, both ``register`` and ``unregister`` can also
    accept a list of model classes in place of a single model; this
    allows easier registration of multiple models with the same
    ``CommentModerator`` class.
    
    The actual moderation is applied in two phases: one prior to
    saving a new comment, and the other immediately after saving. The
    pre-save moderation may mark a comment as non-public or mark it to
    be removed; the post-save moderation may delete a comment which
    was disallowed (there is currently no way to prevent the comment
    being saved once before removal) and, if the comment is still
    around, will send any notification emails the comment generated.
    
    """
    def __init__(self):
        self._registry = {}
        self.connect()
    
    def connect(self):
        """
        Hook up the moderation methods to pre- and post-save signals
        from the comment models.
        
        """
        signals.pre_save.connect(self.pre_save_moderation, sender=comments.get_model())
        signals.post_save.connect(self.post_save_moderation, sender=comments.get_model())
    
    def register(self, model_or_iterable, moderation_class):
        """
        Register a model or a list of models for comment moderation,
        using a particular moderation class.
        
        Raise ``AlreadyModerated`` if any of the models are already
        registered.
        
        """
        if isinstance(model_or_iterable, ModelBase):
            model_or_iterable = [model_or_iterable]
        for model in model_or_iterable:
            if model in self._registry:
                raise AlreadyModerated("The model '%s' is already being moderated" % model._meta.module_name)
            self._registry[model] = moderation_class(model)
    
    def unregister(self, model_or_iterable):
        """
        Remove a model or a list of models from the list of models
        whose comments will be moderated.
        
        Raise ``NotModerated`` if any of the models are not currently
        registered for moderation.
        
        """
        if isinstance(model_or_iterable, ModelBase):
            model_or_iterable = [model_or_iterable]
        for model in model_or_iterable:
            if model not in self._registry:
                raise NotModerated("The model '%s' is not currently being moderated" % model._meta.module_name)
            del self._registry[model]
    
    def pre_save_moderation(self, sender, instance, **kwargs):
        """
        Apply any necessary pre-save moderation steps to new
        comments.
        
        """
        model = instance.content_type.model_class()
        if instance.id or (model not in self._registry):
            return
        content_object = instance.content_object
        moderation_class = self._registry[model]
        if not moderation_class.allow(instance, content_object): # Comment will get deleted in post-save hook.
            instance.moderation_disallowed = True
            return
        if moderation_class.moderate(instance, content_object):
            instance.is_public = False
    
    def post_save_moderation(self, sender, instance, **kwargs):
        """
        Apply any necessary post-save moderation steps to new
        comments.
        
        """
        model = instance.content_type.model_class()
        if model not in self._registry:
            return
        if hasattr(instance, 'moderation_disallowed'):
            instance.delete()
            return
        self._registry[model].email(instance, instance.content_object)

    def comments_open(self, obj):
        """
        Return ``True`` if new comments are being accepted for
        ``obj``, ``False`` otherwise.
        
        If no moderation rules have been registered for the model of
        which ``obj`` is an instance, comments are assumed to be open
        for that object.
        
        """
        model = obj.__class__
        if model not in self._registry:
            return True
        return self._registry[model].comments_open(obj)

    def comments_moderated(self, obj):
        """
        Return ``True`` if new comments for ``obj`` are being
        automatically sent to moderation, ``False`` otherwise.
        
        If no moderation rules have been registered for the model of
        which ``obj`` is an instance, comments for that object are
        assumed not to be moderated.
        
        """
        model = obj.__class__
        if model not in self._registry:
            return False
        return self._registry[model].comments_moderated(obj)


# Import this instance in your own code to use in registering
# your models for moderation.
moderator = Moderator()
