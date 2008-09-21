"""
Custom manager which managers of objects which allow commenting can
inheit from.

"""


from django.db import connection
from django.db import models
from django.utils.datastructures import SortedDict
from django.contrib.contenttypes.models import ContentType
from django.contrib import comments

class CommentedObjectManager(models.Manager):
    """
    A custom manager class which provides useful methods for types of
    objects which allow comments.
    
    Models which allow comments but don't need the overhead of their
    own fully-defined custom manager should use an instance of this
    manager as their default manager.
    
    Models which allow comments and which do have fully-defined custom
    managers should have those managers subclass this one.
    
    """
    def most_commented(self, num=5):
        """
        Returns the ``num`` objects of a given model with the highest
        comment counts, in order.
        
        """
        qn = connection.ops.quote_name
        
        comment_opts = comments.get_model()._meta
        ctype = ContentType.objects.get_for_model(self.model)
        
        subquery = """SELECT COUNT(*)
        FROM %(comment_table)s
        WHERE %(comment_table)s.%(content_type_id)s = %%s
        AND %(comment_table)s.%(object_pk)s = %(self_table)s.%(pk)s
        AND %(comment_table)s.%(is_public)s = %%s
        """ % { 'comment_table': qn(comment_opts.db_table),
                'content_type_id': qn('content_type_id'),
                'object_pk': qn('object_pk'),
                'self_table': qn(self.model._meta.db_table),
                'pk': qn(self.model._meta.pk.name),
                'is_public': qn('is_public'),
                }
        
        return self.extra(select=SortedDict({ 'comment_count': subquery }), select_params=(ctype.id, True,), order_by=['-comment_count'])[:num]
