from distutils.core import setup

setup(name='comment_utils',
      version='0.3p1',
      description='Comment-related utilities for Django applications',
      author='James Bennett',
      author_email='james@b-list.org',
      url='http://code.google.com/p/django-comment-utils/',
      packages=['comment_utils', 'comment_utils.templatetags'],
      classifiers=['Development Status :: 4 - Beta',
                   'Environment :: Web Environment',
                   'Intended Audience :: Developers',
                   'License :: OSI Approved :: BSD License',
                   'Operating System :: OS Independent',
                   'Programming Language :: Python',
                   'Topic :: Utilities'],
      )
