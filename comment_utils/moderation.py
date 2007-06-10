import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import signals
from django.dispatch import dispatcher
from django.template import Context, loader
from django.contrib.comments.models import Comment, FreeComment
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site


registered_models = {}


def register_for_moderation(model, enable_field_name=None, auto_moderate=None, akismet=False, email_notification=False):
    """
    Registers a model for automatic moderation and management.
    
    Pass the model class as the first argument, followed by keyword
    arguments specifying the management options to enable::
    
        akismet
            Causes new comments to instances of this model to be
            subjected to an Akismet spam check before saving; if
            Akismet thinks the comment is spam, its ``is_public``
            field will be set to ``False``. The value of this argument
            should be ``True`` or ``False``.
    
        auto_close
            Causes new comments to instances of this model to be
            automatically rejected when a certain number of days after
            the original publication have elapsed. The value of this
            argument should be a ``DateTimeField`` on the model which
            indicates the publication date.
    
        auto_moderate
            Causes new comments to instances of this model to
            automatically have ``is_public`` set to ``False`` when a
            certain number of days after the original publication have
            elapsed. The value of this argument should be a
            ``DateTimeField`` on the model which indicates the
            publication date.
    
        email_notification
            Causes an email to be sent to each person listed in the
            ``MANAGERS`` setting whenever a new comment is posted to
            an instance of this model. The value of this argument
            should be ``True`` or ``False``.
    
        enable_field_name
            The name of a ``BooleanField`` on the model which is
            ``True`` when comments are open for an instance of the
            model, and ``False`` when comments are closed. When
            comments are closed, no new comments on that object will
            be accepted.
    
    At least one option must be enabled; passing ``False`` values for
    all options, or passing no options, will raise ``ValueError``.
    
    If ``auto_close`` or ``auto_moderate`` are used, you must add the
    setting ``COMMENTS_MODERATE_AFTER`` to your Django settings file;
    the value of this setting should be an integer specifying the
    number of days past publication of an object when ``auto_close``
    or ``auto_moderate`` should begin to operate.
    
    If ``akismet`` is used, you must add the setting
    ``AKISMET_API_KEY`` to your Django settings file; the value of
    this setting should be a valid Akismet API key for use in
    performing Akismet spam checks.
    
    Examples
    --------
    
    Consider the following model::
    
        class Entry(models.Model):
            title = models.CharField(maxlength=250)
            slug = models.SlugField(prepopulate_from=('title',))
            pub_date = models.DateTimeField()
            enable_comments = models.BooleanField()
            body = models.TextField()
    
    To set up automatic Akismet moderation of Entries, register like so::
    
        from comment_utils.moderation import register_for_moderation
        register_for_moderation(Entry, akismet=True)
    
    To have comments automatically rejected when the
    ``enable_comments`` field is ``False``::
    
        register_for_moderation(Entry, enable_field_name='enable_comments')
    
    To have comments automatically be marked non-public when posted to
    Entries published more than ``settings.COMMENTS_MODERATE_AFTER``
    days ago::
    
        register_for_moderation(Entry, auto_moderate='pub_date')
    
    To use all three of these options together::
    
        register_for_moderation(Entry, akismet=True, auto_moderate='pub_date',
                                enable_field_name='enable_comments')
    
    Note that ``auto_close`` and ``auto_moderate`` are exclusive of
    one another; if both are used, ``auto_close`` will "win" and new
    comments will be deleted, rather than be marked non-public.
    
    """
    key = '%s.%s' % (model._meta.app_label, model._meta.module_name)
    if not (akismet or auto_close or auto_moderate or email_notification or enable_field_name):
        raise ValueError("To register the model '%s' for comment moderation, you must specify at least one moderation option")
    registered_models[key] = { 'enable_field_name': enable_field_name,
                               'auto_moderate': auto_moderate,
                               'akismet': akismet,
                               'email_notification': email_notification }


def disallowed_by_field(content_object, field_name):
    return not getattr(content_object, field_name)


def auto_moderated(content_object, field_name):
    return not (datetime.datetime.today() - datetime.timedelta(settings.COMMENTS_MODERATE_AFTER) <= getattr(content_object, field_name))

def akismet_flagged(instance):
    from akismet import Akismet
    akismet_api = Akismet(key=settings.AKISMET_API_KEY,
                          blog_url='http://%s/' % Site.objects.get_current().domain)
    if akismet_api.verify_key():
        akismet_data = { 'comment_type': 'comment',
                         'referrer': '',
                         'user_ip': instance.ip_address,
                         'user_agent': '' }
        return akismet_api.comment_check(instance.comment, data=akismet_data, build_data=True)
    return False

def send_email_notification(instance, object_type):
    recipient_list = [manager_tuple[1] for manager_tuple in settings.MANAGERS]
    t = loader.get_template('comment_utils/comment_notification_email.txt')
    c = Context({ 'comment': instance,
                  'content_object': content_object,
                  'object_type': object_type})
    subject = '[%s] New comment posted on %s "%s"' % (Site.objects.get_current().name,
                                                      object_type,
                                                      str(content_object))
    message = t.render(c)
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=True)
    

def moderate_comments_pre_save(sender, instance):
    """
    Applies comment moderation to comments which are about to be posted.
    
    If the model of the type of object to which the comment is being
    posted has been registered for moderation, moderation is applied
    in the following order:
    
    1. If a field on the model determines whether comments are
       allowed, that field is checked to see whather to allow the
       comment to post. If that field is ``False``, this function
       immediately returns and ``moderate_comments_post_save`` (see
       below) will delete the comment.
    
    2. If the model was registered with ``auto_close`` and the
       requisite number of days since the object's publication have
       passed, this function immediately returns and
       ``moderate_comments_post_save`` will delete the comment.
    
    3. If the model was registered with ``auto_moderate`` and the
       requisite number of days since the object's publication have
       passed, the new comment's ``is_public`` field will be set to
       ``False`` and this function will return immediately afterward.
    
    4. If the model was registered with ``akismet``, the comment will
       be subjected to an Akismet spam check; if Akismet thinks the
       comment is spam, the new comment's ``is_public`` field will be
       set to ``False``.
    
    The explicit returns which may happen after each of the first
    three steps are for efficiency purposes; if one moderation method
    has already determined that the comment should be deleted, or that
    it should be marked non-public, there is no need to consult any
    other methods.
    
    """
    if instance.id:
        return
    ctype = ContentType.objects.get(pk=instance.content_type_id)
    content_object = ctype.get_object_for_this_type(pk=instance.object_id)
    model_key = '%s.%s' % (content_object._meta.app_label, content_object._meta.module_name)
    if model_key not in registered_models:
        return
    moderation_options = registered_models[model_key]
    if moderation_options['enable_field_name'] and disallowed_by_field(content_object, moderation_options['enable_field_name']):
        return
    if moderation_options['auto_close'] and auto_moderated(content_object, moderation_options['auto_close']):
        return
    if moderation_options['auto_moderate'] and auto_moderated(content_object, moderation_options['auto_moderate']):
        instance.is_public = False
        return
    if moderation_options['akismet'] and akismet_flagged(instance):
        instance.is_public = False


def moderate_comments_post_save(sender, instance):
    """
    Applies comment moderation to a comment which has just been posted.
    
    If the model of the type of object to which the comment was posted
    has been registered for moderation, moderation is applied in the
    following order:
    
    1. If the model was registered with ``enable_field_name``, and
       that field on the object is ``False``, the comment is deleted
       and this method immediately returns.
    
    2. If the model was registered with ``auto_close``, and the
       requisite number of days since the object's publication have
       passed, the comment is deleted and this method immediately
       returns.
    
    3. If the model was registered with ``email_notification``, an
       email containing information about the comment is sent to each
       person listed in the ``MANAGERS`` setting.
    
    The explicit returns which may happen after each of the first two
    steps are for efficiency purposes; if one moderation method has
    already determined that the comment should be deleted, there is no
    need to consult any other methods.
    
    """
    content_object = instance.get_content_object()
    model_key = '%s.%s' % (content_object._meta.app_label, content_object._meta.module_name)
    if model_key not in registered_models:
        return
    moderation_options = registered_models[model_key]
    if moderation_options['enable_field_name'] and disallowed_by_field(content_object, moderation_options['enable_field_name']):
        instance.delete()
        return
    if moderation_options['auto_close'] and auto_closed(content_object, moderation_options['auto_close']):
        instance.delete()
        return
    if moderation_options['email_notification']:
        send_email_notification(instance, content_objects._meta.module_name)
