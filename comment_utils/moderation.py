import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import signals
from django.dispatch import dispatcher
from django.template import Context, loader
from django.contrib.comments.models import Comment, FreeComment
from django.contrib.sites.models import Site


def get_model_key(model):
    return "%s.%s" % (model._meta.app_label, model._meta.module_name)


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

class ModeratedModel(object):
    """
    Encapsulates comment-moderation options for a given model.
    
    This class is not designed to be used directly, since it doesn't
    enable any of the available moderation options. Instead, subclass
    it and override attributes to enable different options::
    
        akismet
            If ``True``, comments will be submitted to an Akismet spam
            check and, if Akismet thinks they're spam, will have their
            ``is_public`` field set to ``False`` before saving. If
            this is enabled, you will need to have the Python Akismet
            module installed, and you will need to add the setting
            ``AKISMET_API_KEY`` to your Django settings file; the
            value of this setting should be a valid Akismet API key.
    
        auto_close_field
            If this is set to the name of a ``DateField`` or
            ``DateTimeField`` on the model for which comments are
            being moderated, new comments for objects of that model
            will be disallowed (immediately deleted) when a certain
            number of days have passed after the date specified in
            that field. Must be used in conjunction with
            ``close_after``, which specifies the number of days past
            which comments should be disallowed.
    
        auto_moderate_field
            Like ``auto_close_field``, but instead of outright
            deleting new comments when the requisite number of days
            have elapsed, it will simply set the ``is_public`` field
            of new comments to ``False`` before saving them. Must be
            used in conjunction with ``moderate_after``, which
            specifies the number of days past which comments should be
            moderated.
    
        close_after
            If ``auto_close_field`` is used, this must specify the
            number of days past the value of the field specified by
            ``auto_close_field`` after which new comments for an
            object should be disallowed.
    
        enable_field
            If this is set to the name of a ``BooleanField`` on the
            model for which comments are being moderated, new comments
            on objects of that model will be disallowed (immediately
            deleted) whenever the value of that field is ``False`` on
            the object the comment would be attached to.
    
        moderate_after
            If ``auto_moderate`` is used, this must specify the number
            of days past the value of the field specified by
            ``auto_moderate_field`` after which new comments for an
            object should be marked non-public.
    
    Most common moderation needs can be covered by changing these
    attributes, but further customization can be obtained by
    subclassing and overriding the following methods. Each method will
    be called with two arguments: ``comment``, which is the comment
    being submitted, and ``content_object``, which is the object the
    comment will be attached to::
    
        allow
            Should return ``True`` if the comment should be allowed to
            post on the content object, and ``False`` otherwise (in
            which case the comment will be immediately deleted).
    
        email
            If email notification of the new comment should be sent to
            site staff or moderators, this method is responsible for
            sending the email.

        moderate
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
    enable_field = None
    moderate_after = None
    
    def __init__(self, model):
        self.model = model
    
    def allow(self, comment, content_object):
        if self.enable_field is not None:
            if not getattr(content_object, self.enable_field):
                return False
        if self.auto_close_field and self.close_after:
            if datetime.date.today() - self.auto_close_after > getattr(content_object, self.auto_close_field):
                return False
        return True
    
    def moderate(self, comment, content_object):
        if self.auto_moderate_field and self.moderate_after:
            if datetime.date.today() - self.auto_moderate_after > getattr(content_object, self.auto_moderate_field):
                return True
        if self.akismet:
            from akismet import Akismet
            akismet_api = Akismet(key=settings.AKISMET_API_KEY,
                                  blog_url='http://%s/' % Site.objects.get_current().domain)
            if akismet_api.verify_key():
                akismet_data = { 'comment_type': 'comment',
                                 'referrer': '',
                                 'user_ip': comment.ip_address,
                                 'user_agent': '' }
                if akismet_api.comment_check(instance.comment, data=akismet_data, build_data=True):
                    return True
        return False

    def email(self, comment, content_object):
        recipient_list = [manager_tuple[1] for manager_tuple in settings.MANAGERS]
        t = loader.get_template('comment_utils/comment_notification_email.txt')
        c = Context({ 'comment': comment,
                      'content_object': content_object)
        subject = '[%s] New comment posted on "%s"' % (Site.objects.get_current().name,
                                                          content_object)
        message = t.render(c)
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=True)


class AkismetModeratedModel(ModeratedModel):
    """
    Example subclass of ``ModeratedModel`` which applies Akismet spam
    filtering to comments.
    
    """
    akismet = True


class CommentModerator(object):
    """
    Handles moderation of a set of models.
    
    An instance of this class will maintain a list of one or more
    models registered for comment moderation, and their associated
    moderation classes, and apply moderation to all incoming comments.
    
    To register a model, obtain an instance of ``CommentModerator``
    (this module exports one as ``moderator``), and call its
    ``register`` method, passing the model class and a moderation
    class (which should be a subclass of ``ModeratedModel``). Note
    that both of these should be the actual classes, not instances of
    the classes. If the model is already registered for moderation,
    ``AlreadyModerated`` will be raised.

    To cease moderation for a model, call the ``unregister`` method,
    passing the model class. If the model is not currently being
    moderated, ``NotModerated`` will be raised.

    For convenience, both ``register`` and ``unregister`` can also
    accept a list of model classes in place of a single model; this
    allows easier registration of multiple models with the same
    ``ModeratedModel`` class.

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
    
    def register(self, model_or_iterable, moderation_class):
        if issubclass(model_or_iterable, Model):
            model_or_iterable = [model_or_iterable]
        for model in model_or_iterable:
            model_key = get_model_key(model)
            if model_key in self._registry:
                raise AlreadyModerated("The model '%s.%s' is already being moderated" % model_key)
            self._registry[model_key] = moderation_class(model)
    
    def unregister(self, model_or_iterable):
        if issubclass(model_or_iterable, Model):
            model_or_iterable = [model_or_iterable]
        for model in model_or_iterable:
            model_key = get_model_key(model)
            if model_key not in self._registry:
                raise NotModerated("The model '%s.%s' is not currently being moderated" % model_key)
    
    def pre_save_moderation(self, sender, instance):
        if instance.id:
            return
        content_object = instance.get_content_object()
        model_key = get_model_key(content_object)
        if model_key not in self._registry:
            return
        moderation_class = self._registry(model_key)
        if not moderation_class.allow(instance, content_object): # Comment will get deleted in post-save hook.
            instance.moderation_disallowed = True
            return
        if moderation_class.moderate(instance, content_object):
            instance.is_public = False

    def post_save_moderation(self, sender, instance):
        content_object = instance.get_content_object()
        model_key = get_model_key(content_object)
        if model_key not in self._registry:
            return
        if instance.moderation_disallowed:
            instance.delete()
            return
        moderation_class.email(instance, content_object)


moderator = CommentModerator()


dispatcher.connect(moderater.pre_save_moderation, sender=Comment, signal=signals.pre_save)
dispatcher.connect(moderator.post_save_moderation, sender=Comment, signal=signals.post_save)
dispatcher.connect(moderater.pre_save_moderation, sender=FreeComment, signal=signals.pre_save)
dispatcher.connect(moderator.post_save_moderation, sender=FreeComment, signal=signals.post_save)
