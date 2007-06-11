"""
Custom manager which managers of objects which allow commenting can
inheit from.

"""

from django.db import models

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
    def most_commented(self, num=5, free=True):
        """
        Returns the ``num`` objects of a given model with the highest
        comment counts, in order.
        
        Pass ``free=False`` if you're using the registered comment
        model (``Comment``) instead of the anonymous comment model
        (``FreeComment``).
        
        """
        from django.db import backend, connection
        from django.contrib.comments import models as comment_models
        from django.contrib.contenttypes.models import ContentType
        if free:
            comment_opts = comment_models.FreeComment._meta
        else:
            comment_opts = comment_models.Comment._meta
            ctype = ContentType.objects.get_for_model(self.model)
            query = """SELECT %s, COUNT(*) AS score
            FROM %s
            WHERE content_type_id = %%s
            AND is_public = 1
            GROUP BY %s
            ORDER BY score DESC""" % (backend.quote_name('object_id'),
                                      backend.quote_name(comment_opts.db_table),
                                      backend.quote_name('object_id'),)
            
            cursor = connection.cursor()
            cursor.execute(query, [ctype.id])
            entry_ids = [row[0] for row in cursor.fetchall()[:num]]
            
            # Use ``in_bulk`` here instead of an ``id__in`` filter, because ``id__in``
            # would clobber the ordering.
            entry_dict = self.in_bulk(entry_ids)
            return [entry_dict[entry_id] for entry_id in entry_ids]
