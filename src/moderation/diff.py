# -*- coding: utf-8 -*-

import re
import difflib

from django.db.models import fields, ForeignKey, FileField
from django.utils.html import escape
from django.core.urlresolvers import reverse, NoReverseMatch


class BaseChange(object):

    def __repr__(self):
        value1, value2 = self.change
        return u'Change object: %s - %s' % (value1, value2)

    def __init__(self, verbose_name, field, change):
        self.verbose_name = verbose_name
        self.field = field
        self.change = change

    def render_diff(self, template, context):
        from django.template.loader import render_to_string

        return render_to_string(template, context)

class ForeignKeyChange(BaseChange):
    @property
    def diff(self):
        rel_to = self.field.rel.to
        pk1, pk2 = self.change
        value1, value2 = rel_to.objects.get(pk=self.change[0]), rel_to.objects.get(pk=self.change[1])
        
        try:
            url1 = reverse( 'admin:%s_%s_change' % (rel_to._meta.app_label, rel_to._meta.object_name.lower()),args=[pk1] ) 
        except NoReverseMatch:
            url1 = None

        if value1 == value2:
            if url1:
                return '<a href="%(url)s">%(value)s</a>' % {'url': url1, 'value':value1}
            return value1

        try:
            url2 = reverse( 'admin:%s_%s_change' % (rel_to._meta.app_label, rel_to._meta.object_name.lower()),args=[pk2] ) 
        except NoReverseMatch:
            url2 = None
        
        if url1:
            return ('<a href="%(url)s">%(value)s</a>' % {'url': url1, 'value':value1})+ ' &gt; ' +('<a href="%(url)s">%(value)s</a>' % {'url': url2, 'value':value2})
        return value1+' &gt; '+value2
        
class FileChange(BaseChange):
    
    @property
    def diff(self):
        value1, value2 = (unicode(self.change[0]), unicode(self.change[1]))
        url1, url2 = (self.change[0].url, self.change[1].url)
        change = (value1, value2)
        if value1 == value2:
            return '<a href="%(url)s">%(value)s</a>' % {'url': url1, 'value':value1}
        return ('<a href="%(url)s">%(value)s</a>' % {'url': url1, 'value':value1})+' &gt; '+('<a href="%(url)s">%(value)s</a>' % {'url': url2, 'value':value2})
    
class TextChange(BaseChange):

    @property
    def diff(self):
        value1, value2 = escape(self.change[0]), escape(self.change[1])
        if value1 == value2:
            return value1

        return self.render_diff(
            'moderation/html_diff.html',
                {'diff_operations': get_diff_operations(*self.change)})



class ImageChange(BaseChange):
    @property
    def diff(self):
        left_image, right_image = self.change
        return self.render_diff(
            'moderation/image_diff.html',
                {'left_image': left_image,
                 'right_image': right_image})


def get_change(model1, model2, field):
    try:
        value1 = getattr(model1, "get_%s_display" % field.name)()
        value2 = getattr(model2, "get_%s_display" % field.name)()
    except AttributeError:
        value1 = field.value_from_object(model1)
        value2 = field.value_from_object(model2)

    change = get_change_for_type(
        field.verbose_name,
            (value1, value2),
        field,
        )

    return change


def get_changes_between_models(model1, model2, excludes=[]):
    changes = {}

    for field in model1._meta.fields:
        if not (isinstance(field, (fields.AutoField,))):
            if field.name in excludes:
                continue

            name = u"%s__%s" % (model1.__class__.__name__.lower(), field.name)

            changes[name] = get_change(model1, model2, field)

    return changes


def get_diff_operations(a, b):
    operations = []
    a_words = re.split('(\W+)', a)
    b_words = re.split('(\W+)', b)
    sequence_matcher = difflib.SequenceMatcher(None, a_words, b_words)
    for opcode in sequence_matcher.get_opcodes():
        operation, start_a, end_a, start_b, end_b = opcode

        deleted = ''.join(a_words[start_a:end_a])
        inserted = ''.join(b_words[start_b:end_b])

        operations.append({'operation': operation,
                           'deleted': deleted,
                           'inserted': inserted})
    return operations


def html_to_list(html):
    pattern = re.compile(r'&.*?;|(?:<[^<]*?>)|'\
                         '(?:\w[\w-]*[ ]*)|(?:<[^<]*?>)|'\
                         '(?:\s*[,\.\?]*)', re.UNICODE)

    return [''.join(element) for element in filter(None,
                                                   pattern.findall(html))]


def get_change_for_type(verbose_name, change, field):
    if isinstance(field, fields.files.ImageField):
        change = ImageChange(
            u"Current %(verbose_name)s / "\
            u"New %(verbose_name)s" % {'verbose_name': verbose_name},
            field,
            change)
    elif isinstance(field, ForeignKey):
        value1, value2 = change
        change = ForeignKeyChange(verbose_name, field, (unicode(value1), unicode(value2)))
    elif isinstance(field, FileField):
        value1, value2 = change
        change = FileChange(verbose_name, field, (value1, value2))
    else:
        value1, value2 = change
        change = TextChange(verbose_name, field, (unicode(value1), unicode(value2)))
    return change
