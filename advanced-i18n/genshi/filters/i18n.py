# -*- coding: utf-8 -*-
#
# Copyright (C) 2007 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://genshi.edgewall.org/wiki/License.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://genshi.edgewall.org/log/.

"""Utilities for internationalization and localization of templates.

:since: version 0.4
"""

from compiler import ast
from gettext import NullTranslations
import re
from types import FunctionType

from genshi.core import Attrs, Namespace, QName, START, END, TEXT, START_NS, \
                        END_NS, XML_NAMESPACE, _ensure
from genshi.template.base import DirectiveFactory, EXPR, SUB, _apply_directives
from genshi.template.directives import Directive
from genshi.template.markup import MarkupTemplate, EXEC

__all__ = ['Translator', 'extract']
__docformat__ = 'restructuredtext en'

I18N_NAMESPACE = Namespace('http://genshi.edgewall.org/i18n')


class CommentDirective(Directive):

    __slots__ = []

    @classmethod
    def attach(cls, template, stream, value, namespaces, pos):
        return None, stream


class MsgDirective(Directive):

    __slots__ = ['params']

    def __init__(self, value, template, hints=None, namespaces=None,
                 lineno=-1, offset=-1):
        Directive.__init__(self, None, template, namespaces, lineno, offset)
        self.params = [name.strip() for name in value.split(',')]

    def __call__(self, stream, directives, ctxt, **vars):
        
        msgbuf = MessageBuffer(self.params)

        stream = iter(stream)
        yield stream.next() # the outer start tag
        previous = stream.next()
        for event in stream:
            msgbuf.append(*previous)
            previous = event

        gettext = ctxt.get('_i18n.gettext')
        dgettext = ctxt.get('_i18n.dgettext')
        if ctxt.get('_i18n.domain'):
            assert callable(dgettext), "No domain gettext function passed"
            gettext = lambda msg: dgettext(ctxt.get('_i18n.domain'), msg)

        for event in msgbuf.translate(gettext(msgbuf.format())):
            yield event

        yield previous # the outer end tag


class DomainDirective(Directive):

    __slots__ = ['domain']

    def __init__(self, value, template, hints=None, namespaces=None,
                 lineno=-1, offset=-1):
        Directive.__init__(self, None, template, namespaces, lineno, offset)
        self.domain = value
        
    @classmethod
    def attach(cls, template, stream, value, namespaces, pos):
        if type(value) is dict:
            value = value.get('name')
        return super(DomainDirective, cls).attach(template, stream, value,
                                                  namespaces, pos)

    def __call__(self, stream, directives, ctxt, **vars):            
        ctxt.push({'_i18n.domain': self.domain})        
        for event in stream:
            yield event
        ctxt.pop()

class Translator(DirectiveFactory):
    """Can extract and translate localizable strings from markup streams and
    templates.
    
    For example, assume the following template:
    
    >>> from genshi.template import MarkupTemplate
    >>> 
    >>> tmpl = MarkupTemplate('''<html xmlns:py="http://genshi.edgewall.org/">
    ...   <head>
    ...     <title>Example</title>
    ...   </head>
    ...   <body>
    ...     <h1>Example</h1>
    ...     <p>${_("Hello, %(name)s") % dict(name=username)}</p>
    ...   </body>
    ... </html>''', filename='example.html')
    
    For demonstration, we define a dummy ``gettext``-style function with a
    hard-coded translation table, and pass that to the `Translator` initializer:
    
    >>> def pseudo_gettext(string):
    ...     return {
    ...         'Example': 'Beispiel',
    ...         'Hello, %(name)s': 'Hallo, %(name)s'
    ...     }[string]
    >>> 
    >>> translator = Translator(pseudo_gettext)
    
    Next, the translator needs to be prepended to any already defined filters
    on the template:
    
    >>> tmpl.filters.insert(0, translator)
    
    When generating the template output, our hard-coded translations should be
    applied as expected:
    
    >>> print tmpl.generate(username='Hans', _=pseudo_gettext)
    <html>
      <head>
        <title>Beispiel</title>
      </head>
      <body>
        <h1>Beispiel</h1>
        <p>Hallo, Hans</p>
      </body>
    </html>

    Note that elements defining ``xml:lang`` attributes that do not contain
    variable expressions are ignored by this filter. That can be used to
    exclude specific parts of a template from being extracted and translated.
    """

    directives = [
        ('domain', DomainDirective),
        ('comment', CommentDirective),
        ('msg', MsgDirective)
    ]

    IGNORE_TAGS = frozenset([
        QName('script'), QName('http://www.w3.org/1999/xhtml}script'),
        QName('style'), QName('http://www.w3.org/1999/xhtml}style')
    ])
    INCLUDE_ATTRS = frozenset(['abbr', 'alt', 'label', 'prompt', 'standby',
                               'summary', 'title'])
    NAMESPACE = I18N_NAMESPACE

    def __init__(self, translate=NullTranslations(), ignore_tags=IGNORE_TAGS,
                 include_attrs=INCLUDE_ATTRS, extract_text=True):
        """Initialize the translator.
        
        :param translate: the translation function, for example ``gettext`` or
                          ``ugettext``.
        :param ignore_tags: a set of tag names that should not be localized
        :param include_attrs: a set of attribute names should be localized
        :param extract_text: whether the content of text nodes should be
                             extracted, or only text in explicit ``gettext``
                             function calls

        :note: Changed in 0.6: the `translate` parameter can now be either
               a ``gettext``-style function, or an object compatible with the
               ``NullTransalations`` or ``GNUTranslations`` interface
        """
        self.translate = translate
        self.ignore_tags = ignore_tags
        self.include_attrs = include_attrs
        self.extract_text = extract_text

    def __call__(self, stream, ctxt=None, search_text=True):
        """Translate any localizable strings in the given stream.
        
        This function shouldn't be called directly. Instead, an instance of
        the `Translator` class should be registered as a filter with the
        `Template` or the `TemplateLoader`, or applied as a regular stream
        filter. If used as a template filter, it should be inserted in front of
        all the default filters.
        
        :param stream: the markup event stream
        :param ctxt: the template context (not used)
        :param search_text: whether text nodes should be translated (used
                            internally)
        :return: the localized stream
        """
        ignore_tags = self.ignore_tags
        include_attrs = self.include_attrs
        skip = 0
        xml_lang = XML_NAMESPACE['lang']

        if type(self.translate) is FunctionType:
            gettext = self.translate
        else:
            gettext = self.translate.ugettext
            try:
                dgettext = self.translate.dugettext
            except AttributeError:
                dgettext = lambda x, y: gettext(y)
                
        if ctxt:
            ctxt['_i18n.gettext'] = gettext
            ctxt['_i18n.dgettext'] = dgettext

        extract_text = self.extract_text
        if not extract_text:
            search_text = False

        for kind, data, pos in stream:
            
            # skip chunks that should not be localized
            if skip:
                if kind is START:
                    skip += 1
                elif kind is END:
                    skip -= 1
                yield kind, data, pos
                continue

            # handle different events that can be localized
            if kind is START:
                tag, attrs = data
                if tag in self.ignore_tags or \
                        isinstance(attrs.get(xml_lang), basestring):
                    skip += 1
                    yield kind, data, pos
                    continue

                new_attrs = []
                changed = False
                for name, value in attrs:
                    newval = value
                    if extract_text and isinstance(value, basestring):
                        if name in include_attrs:
                            newval = gettext(value)
                    else:
                        newval = list(
                            self(_ensure(value), ctxt, search_text=False)
                        )
                    if newval != value:
                        value = newval
                        changed = True
                    new_attrs.append((name, value))
                if changed:
                    attrs = Attrs(new_attrs)

                yield kind, (tag, attrs), pos

            elif search_text and kind is TEXT:
                if ctxt and ctxt.get('_i18n.domain'):
                    old_gettext = gettext
                    gettext = lambda x: dgettext(ctxt.get('_i18n.domain'), x)
                text = data.strip()
                if text:
                    data = data.replace(text, unicode(gettext(text)))
                yield kind, data, pos
                if ctxt and ctxt.get('_i18n.domain'):
                    gettext = old_gettext

            elif kind is SUB:
                directives, substream = data
                current_domain = [d.domain for d in directives if
                                  isinstance(d, DomainDirective)]
                if current_domain:
                    ctxt.push({'_i18n.domain': current_domain[0]})

                # If this is an i18n:msg directive, no need to translate text
                # nodes here
                is_msg = filter(None, [isinstance(d, MsgDirective)
                                       for d in directives])
                substream = list(self(substream, ctxt,
                                      search_text=not is_msg))
                yield kind, (directives, substream), pos
                
                if current_domain:
                    ctxt.pop()
            else:
                yield kind, data, pos

    GETTEXT_FUNCTIONS = ('_', 'gettext', 'ngettext', 'dgettext', 'dngettext',
                         'ugettext', 'ungettext')

    def extract(self, stream, gettext_functions=GETTEXT_FUNCTIONS,
                search_text=True, msgbuf=None):
        """Extract localizable strings from the given template stream.
        
        For every string found, this function yields a ``(lineno, function,
        message, comments)`` tuple, where:
        
        * ``lineno`` is the number of the line on which the string was found,
        * ``function`` is the name of the ``gettext`` function used (if the
          string was extracted from embedded Python code), and
        *  ``message`` is the string itself (a ``unicode`` object, or a tuple
           of ``unicode`` objects for functions with multiple string
           arguments).
        *  ``comments`` is a list of comments related to the message, extracted
           from ``i18n:comment`` attributes found in the markup
        
        >>> from genshi.template import MarkupTemplate
        >>> 
        >>> tmpl = MarkupTemplate('''<html xmlns:py="http://genshi.edgewall.org/">
        ...   <head>
        ...     <title>Example</title>
        ...   </head>
        ...   <body>
        ...     <h1>Example</h1>
        ...     <p>${_("Hello, %(name)s") % dict(name=username)}</p>
        ...     <p>${ngettext("You have %d item", "You have %d items", num)}</p>
        ...   </body>
        ... </html>''', filename='example.html')
        >>> 
        >>> for line, func, msg, comments in Translator().extract(tmpl.stream):
        ...    print "%d, %r, %r" % (line, func, msg)
        3, None, u'Example'
        6, None, u'Example'
        7, '_', u'Hello, %(name)s'
        8, 'ngettext', (u'You have %d item', u'You have %d items', None)
        
        :param stream: the event stream to extract strings from; can be a
                       regular stream or a template stream
        :param gettext_functions: a sequence of function names that should be
                                  treated as gettext-style localization
                                  functions
        :param search_text: whether the content of text nodes should be
                            extracted (used internally)
        
        :note: Changed in 0.4.1: For a function with multiple string arguments
               (such as ``ngettext``), a single item with a tuple of strings is
               yielded, instead an item for each string argument.
        :note: Changed in 0.6: The returned tuples now include a 4th element,
               which is a list of comments for the translator
        """
        if not self.extract_text:
            search_text = False
        skip = 0
        i18n_comment = I18N_NAMESPACE['comment']
        i18n_msg = I18N_NAMESPACE['msg']
        xml_lang = XML_NAMESPACE['lang']

        for kind, data, pos in stream:

            if skip:
                if kind is START:
                    skip += 1
                if kind is END:
                    skip -= 1

            if kind is START and not skip:
                tag, attrs = data

                if tag in self.ignore_tags or \
                        isinstance(attrs.get(xml_lang), basestring):
                    skip += 1
                    continue

                for name, value in attrs:
                    if search_text and isinstance(value, basestring):
                        if name in self.include_attrs:
                            text = value.strip()
                            if text:
                                yield pos[1], None, text, []
                    else:
                        for lineno, funcname, text, comments in self.extract(
                                _ensure(value), gettext_functions,
                                search_text=False):
                            yield lineno, funcname, text, comments

                if msgbuf:
                    msgbuf.append(kind, data, pos)
                else:
                    msg_params = attrs.get(i18n_msg)
                    if msg_params is not None:
                        if type(msg_params) is list: # event tuple
                            msg_params = msg_params[0][1]
                        msgbuf = MessageBuffer(
                            msg_params, attrs.get(i18n_comment), pos[1]
                        )

            elif not skip and search_text and kind is TEXT:
                if not msgbuf:
                    text = data.strip()
                    if text and filter(None, [ch.isalpha() for ch in text]):
                        yield pos[1], None, text, []
                else:
                    msgbuf.append(kind, data, pos)

            elif not skip and msgbuf and kind is END:
                msgbuf.append(kind, data, pos)
                if not msgbuf.depth:
                    yield msgbuf.lineno, None, msgbuf.format(), \
                          filter(None, [msgbuf.comment])
                    msgbuf = None

            elif kind is EXPR or kind is EXEC:
                if msgbuf:
                    msgbuf.append(kind, data, pos)
                for funcname, strings in extract_from_code(data,
                                                           gettext_functions):
                    yield pos[1], funcname, strings, []

            elif kind is SUB:
                subkind, substream = data
                messages = self.extract(substream, gettext_functions,
                                        search_text=search_text and not skip,
                                        msgbuf=msgbuf)
                for lineno, funcname, text, comments in messages:
                    yield lineno, funcname, text, comments


class MessageBuffer(object):
    """Helper class for managing internationalized mixed content.
    
    :since: version 0.5
    """

    def __init__(self, params=u'', comment=None, lineno=-1):
        """Initialize the message buffer.
        
        :param params: comma-separated list of parameter names
        :type params: `basestring`
        :param lineno: the line number on which the first stream event
                       belonging to the message was found
        """
        if isinstance(params, basestring):
            params = [name.strip() for name in params.split(',')]
        self.params = params
        self.comment = comment
        self.lineno = lineno
        self.string = []
        self.events = {}
        self.values = {}
        self.depth = 1
        self.order = 1
        self.stack = [0]

    def append(self, kind, data, pos):
        """Append a stream event to the buffer.
        
        :param kind: the stream event kind
        :param data: the event data
        :param pos: the position of the event in the source
        """
        if kind is TEXT:
            self.string.append(data)
            self.events.setdefault(self.stack[-1], []).append(None)
        elif kind is EXPR:
            param = self.params.pop(0)
            self.string.append('%%(%s)s' % param)
            self.events.setdefault(self.stack[-1], []).append(None)
            self.values[param] = (kind, data, pos)
        else:
            if kind is START:
                self.string.append(u'[%d:' % self.order)
                self.events.setdefault(self.order, []).append((kind, data, pos))
                self.stack.append(self.order)
                self.depth += 1
                self.order += 1
            elif kind is END:
                self.depth -= 1
                if self.depth:
                    self.events[self.stack[-1]].append((kind, data, pos))
                    self.string.append(u']')
                    self.stack.pop()

    def format(self):
        """Return a message identifier representing the content in the
        buffer.
        """
        return u''.join(self.string).strip()

    def translate(self, string, regex=re.compile(r'%\((\w+)\)s')):
        """Interpolate the given message translation with the events in the
        buffer and return the translated stream.
        
        :param string: the translated message string
        """
        parts = parse_msg(string)
        for order, string in parts:
            events = self.events[order]
            while events:
                event = events.pop(0)
                if event:
                    yield event
                else:
                    if not string:
                        break
                    for idx, part in enumerate(regex.split(string)):
                        if idx % 2:
                            yield self.values[part]
                        elif part:
                            yield TEXT, part, (None, -1, -1)
                    if not self.events[order] or not self.events[order][0]:
                        break


def parse_msg(string, regex=re.compile(r'(?:\[(\d+)\:)|\]')):
    """Parse a translated message using Genshi mixed content message
    formatting.

    >>> parse_msg("See [1:Help].")
    [(0, 'See '), (1, 'Help'), (0, '.')]

    >>> parse_msg("See [1:our [2:Help] page] for details.")
    [(0, 'See '), (1, 'our '), (2, 'Help'), (1, ' page'), (0, ' for details.')]

    >>> parse_msg("[2:Details] finden Sie in [1:Hilfe].")
    [(2, 'Details'), (0, ' finden Sie in '), (1, 'Hilfe'), (0, '.')]

    >>> parse_msg("[1:] Bilder pro Seite anzeigen.")
    [(1, ''), (0, ' Bilder pro Seite anzeigen.')]

    :param string: the translated message string
    :return: a list of ``(order, string)`` tuples
    :rtype: `list`
    """
    parts = []
    stack = [0]
    while True:
        mo = regex.search(string)
        if not mo:
            break

        if mo.start() or stack[-1]:
            parts.append((stack[-1], string[:mo.start()]))
        string = string[mo.end():]

        orderno = mo.group(1)
        if orderno is not None:
            stack.append(int(orderno))
        else:
            stack.pop()
        if not stack:
            break

    if string:
        parts.append((stack[-1], string))

    return parts


def extract_from_code(code, gettext_functions):
    """Extract strings from Python bytecode.
    
    >>> from genshi.template.eval import Expression
    
    >>> expr = Expression('_("Hello")')
    >>> list(extract_from_code(expr, Translator.GETTEXT_FUNCTIONS))
    [('_', u'Hello')]

    >>> expr = Expression('ngettext("You have %(num)s item", '
    ...                            '"You have %(num)s items", num)')
    >>> list(extract_from_code(expr, Translator.GETTEXT_FUNCTIONS))
    [('ngettext', (u'You have %(num)s item', u'You have %(num)s items', None))]
    
    :param code: the `Code` object
    :type code: `genshi.template.eval.Code`
    :param gettext_functions: a sequence of function names
    :since: version 0.5
    """
    def _walk(node):
        if isinstance(node, ast.CallFunc) and isinstance(node.node, ast.Name) \
                and node.node.name in gettext_functions:
            strings = []
            def _add(arg):
                if isinstance(arg, ast.Const) \
                        and isinstance(arg.value, basestring):
                    strings.append(unicode(arg.value, 'utf-8'))
                elif arg and not isinstance(arg, ast.Keyword):
                    strings.append(None)
            [_add(arg) for arg in node.args]
            _add(node.star_args)
            _add(node.dstar_args)
            if len(strings) == 1:
                strings = strings[0]
            else:
                strings = tuple(strings)
            yield node.node.name, strings
        else:
            for child in node.getChildNodes():
                for funcname, strings in _walk(child):
                    yield funcname, strings
    return _walk(code.ast)


def extract(fileobj, keywords, comment_tags, options):
    """Babel extraction method for Genshi templates.
    
    :param fileobj: the file-like object the messages should be extracted from
    :param keywords: a list of keywords (i.e. function names) that should be
                     recognized as translation functions
    :param comment_tags: a list of translator tags to search for and include
                         in the results
    :param options: a dictionary of additional options (optional)
    :return: an iterator over ``(lineno, funcname, message, comments)`` tuples
    :rtype: ``iterator``
    """
    template_class = options.get('template_class', MarkupTemplate)
    if isinstance(template_class, basestring):
        module, clsname = template_class.split(':', 1)
        template_class = getattr(__import__(module, {}, {}, [clsname]), clsname)
    encoding = options.get('encoding', None)

    extract_text = options.get('extract_text', True)
    if isinstance(extract_text, basestring):
        extract_text = extract_text.lower() in ('1', 'on', 'yes', 'true')

    ignore_tags = options.get('ignore_tags', Translator.IGNORE_TAGS)
    if isinstance(ignore_tags, basestring):
        ignore_tags = ignore_tags.split()
    ignore_tags = [QName(tag) for tag in ignore_tags]

    include_attrs = options.get('include_attrs', Translator.INCLUDE_ATTRS)
    if isinstance(include_attrs, basestring):
        include_attrs = include_attrs.split()
    include_attrs = [QName(attr) for attr in include_attrs]

    tmpl = template_class(fileobj, filename=getattr(fileobj, 'name', None),
                          encoding=encoding)
    translator = Translator(None, ignore_tags, include_attrs, extract_text)
    for message in translator.extract(tmpl.stream, gettext_functions=keywords):
        yield message
