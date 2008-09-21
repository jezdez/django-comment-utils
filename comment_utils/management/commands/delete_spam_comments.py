import datetime
from optparse import make_option

from django.contrib import comments
from django.core.management.base import NoArgsCommand

def delete_spam_comments(age, dry_run, verbosity):
    age_cutoff = datetime.datetime.now() - datetime.timedelta(days=age)
    comments_to_delete = comments.get_model().objects.filter(is_public__exact=False,
                                                             submit_date__lt=age_cutoff)
    deleted_count = comments_to_delete.count()
    if not dry_run:
        for comment in comments_to_delete:
            if verbosity > 1:
                print "Deleting spam comment '%s' on '%s', from %s" % (comment,
                                                                       comment.content_object,
                                                                       comment.submit_date.strftime("%Y-%m-%d"))
            comment.delete()
    print "Deleted %s spam comments" % deleted_count


class Command(NoArgsCommand):
    option_list = NoArgsCommand.option_list + (
        make_option('-a', '--age', dest='age', type='int', default=14,
                    help='The age threshold, in days, past which a non-public comment will be considered spam, and thus be deleted. Defaults to 14 if not supplied.'),
        make_option('-d', '--dry-run', action="store_true", dest="dry_run",
                    help='Does not delete any comments, but merely outputs the number of comments which would have been deleted.'),
        make_option('-v', '--verbosity', action='store', dest='verbosity', default='1',
                    type='choice', choices=['0', '1', '2'],
                    help='Verbosity level; 0=minimal output, 1=normal output, 2=all output'),
    )
    help = "Removes spam comments from the database."

    def handle_noargs(self, **options):
        verbosity = int(options.get('verbosity', 1))
        interactive = options.get('interactive')
        age = options.get('age', 14)
        dry_run = options.get('dry_run', False)
        verbose = options.get('verbosity', 1)
        delete_spam_comments(age=age, dry_run=dry_run, verbosity=verbosity)
