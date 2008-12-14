# -*- coding: utf-8 -*-
#
# Copyright (C) 2008 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://genshi.edgewall.org/wiki/License.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://genshi.edgewall.org/log/.

"""Module checking for _ast and if that's not present uses emulation
based on compiler.ast module.
"""

__docformat__ = 'restructuredtext en'

import compiler
import compiler.ast

from genshi.template import _ast24 as _ast


def _new(cls, *args, **kwargs):
    ret = cls()
    if ret._fields:
        for attr, value in zip(ret._fields, args):
            if attr in kwargs:
                raise ValueError, "Field set both in args and kwargs"
            setattr(ret, attr, value)
    for attr in kwargs:
        if (getattr(ret, '_fields', None) and attr in ret._fields) \
                or (getattr(ret, '_attributes', None) and 
                        attr in ret._attributes):
            setattr(ret, attr, kwargs[attr])
    return ret


class ASTUpgrader(object):
    """Transformer changing structure of Python 2.4 ASTs to
    Python 2.5 ones.

    Transforms ``compiler.ast`` Abstract Syntax Tree to builtin ``_ast``.
    It can use fake _ast classes and this way allow _ast emulation
    in Python 2.4"""

    def __init__(self):
        self.out_flags = None
        self.lines = [-1]

    def _new(self, *args, **kwargs):
        return _new(lineno = self.lines[-1], *args, **kwargs)

    def visit(self, node):
        if node is None:
            return None
        if type(node) is tuple:
            return tuple([self.visit(n) for n in node])
        lno = getattr(node, 'lineno', None)
        if lno is not None:
            self.lines.append(lno)
        visitor = getattr(self, 'visit%s' % node.__class__.__name__,
                          self._visitDefault)

        retval = visitor(node)
        if lno is not None:
            self.lines.pop()
        return retval

    def _visitDefault(self, node):
        assert False, node

    def visitModule(self, node):
        body = self.visit(node.node)
        if node.doc:
            body = [self._new(_ast.Expr, self._new(_ast.Str, node.doc))] + body
        return self._new(_ast.Module, body)

    def visitExpression(self, node):
        return self._new(_ast.Expression, self.visit(node.node))

    def _extract_args(self, node):
        tab = node.argnames[:]
        if node.flags & compiler.ast.CO_VARKEYWORDS:
            kwarg = tab[-1]
            tab = tab[:-1]
        else:
            kwarg = None

        if node.flags & compiler.ast.CO_VARARGS:
            vararg = tab[-1]
            tab = tab[:-1]
        else:
            vararg = None

        def _tup(t):
            if isinstance(t, str):
                return self._new(_ast.Name, t, _ast.Store())
            elif isinstance(t, tuple):
                elts = [_tup(x) for x in t]
                return self._new(_ast.Tuple, elts, _ast.Store())
            else:
                raise NotImplemented
            
        args = []
        for arg in tab:
            if isinstance(arg, str):
                args.append(self._new(_ast.Name, arg, _ast.Param()))
            elif isinstance(arg, tuple):
                args.append(_tup(arg))
            else:
                assert False, node.__class__

        defaults = map(self.visit, node.defaults)
        return self._new(_ast.arguments, args, vararg, kwarg, defaults)


    def visitFunction(self, node):
        if getattr(node, 'decorators', ()):
            decorators = [self.visit(d) for d in node.decorators.nodes]
        else:
            decorators = []

        args = self._extract_args(node)
        body = self.visit(node.code)
        if node.doc:
            body = [self._new(_ast.Expr, self._new(_ast.Str, node.doc))] + body
        return self._new(_ast.FunctionDef, node.name, args, body, decorators)

    def visitClass(self, node):
        #self.name_types.append(_ast.Load)
        bases = [self.visit(b) for b in node.bases]
        #self.name_types.pop()
        body = self.visit(node.code)
        if node.doc:
            body = [self._new(_ast.Expr, self._new(_ast.Str, node.doc))] + body
        return self._new(_ast.ClassDef, node.name, bases, body)

    def visitReturn(self, node):
        return self._new(_ast.Return, self.visit(node.value))

    def visitAssign(self, node):
        #self.name_types.append(_ast.Store)
        targets = [self.visit(t) for t in node.nodes]
        #self.name_types.pop()
        return self._new(_ast.Assign, targets, self.visit(node.expr))

    aug_operators = {
        '+=': _ast.Add,
        '/=': _ast.Div,
        '//=': _ast.FloorDiv,
        '<<=': _ast.LShift,
        '%=': _ast.Mod,
        '*=': _ast.Mult,
        '**=': _ast.Pow,
        '>>=': _ast.RShift,
        '-=': _ast.Sub,
    }

    def visitAugAssign(self, node):
        target = self.visit(node.node)

        # Because it's AugAssign target can't be list nor tuple
        # so we only have to change context of one node
        target.ctx = _ast.Store()
        op = self.aug_operators[node.op]()
        return self._new(_ast.AugAssign, target, op, self.visit(node.expr))

    def _visitPrint(nl):
        def _visit(self, node):
            values = [self.visit(v) for v in node.nodes]
            return self._new(_ast.Print, self.visit(node.dest), values, nl)
        return _visit

    visitPrint = _visitPrint(False)
    visitPrintnl = _visitPrint(True)
    del _visitPrint

    def visitFor(self, node):
        return self._new(_ast.For, self.visit(node.assign), self.visit(node.list),
                        self.visit(node.body), self.visit(node.else_))

    def visitWhile(self, node):
        return self._new(_ast.While, self.visit(node.test), self.visit(node.body),
                        self.visit(node.else_))

    def visitIf(self, node):
        def _level(tests, else_):
            test = self.visit(tests[0][0])
            body = self.visit(tests[0][1])
            if len(tests) == 1:
                orelse = self.visit(else_)
            else:
                orelse = [_level(tests[1:], else_)]
            return self._new(_ast.If, test, body, orelse)
        return _level(node.tests, node.else_)

    def visitWith(self, node):
        return self._new(_ast.With, self.visit(node.expr),
                            self.visit(node.vars), self.visit(node.body))

    def visitRaise(self, node):
        return self._new(_ast.Raise, self.visit(node.expr1),
                        self.visit(node.expr2), self.visit(node.expr3))

    def visitTryExcept(self, node):
        handlers = []
        for type, name, body in node.handlers:
            handlers.append(self._new(_ast.excepthandler, self.visit(type), 
                            self.visit(name), self.visit(body)))
        return self._new(_ast.TryExcept, self.visit(node.body),
                        handlers, self.visit(node.else_))

    def visitTryFinally(self, node):
        return self._new(_ast.TryFinally, self.visit(node.body),
                        self.visit(node.final))

    def visitAssert(self, node):
        return self._new(_ast.Assert, self.visit(node.test), self.visit(node.fail))

    def visitImport(self, node):
        names = [self._new(_ast.alias, n[0], n[1]) for n in node.names]
        return self._new(_ast.Import, names)

    def visitFrom(self, node):
        names = [self._new(_ast.alias, n[0], n[1]) for n in node.names]
        return self._new(_ast.ImportFrom, node.modname, names, 0)

    def visitExec(self, node):
        return self._new(_ast.Exec, self.visit(node.expr),
                        self.visit(node.locals), self.visit(node.globals))

    def visitGlobal(self, node):
        return self._new(_ast.Global, node.names[:])

    def visitDiscard(self, node):
        return self._new(_ast.Expr, self.visit(node.expr))

    def _map_class(to):
        def _visit(self, node):
            return self._new(to)
        return _visit

    visitPass = _map_class(_ast.Pass)
    visitBreak = _map_class(_ast.Break)
    visitContinue = _map_class(_ast.Continue)

    def _visitBinOperator(opcls):
        def _visit(self, node):
            return self._new(_ast.BinOp, self.visit(node.left), 
                            opcls(), self.visit(node.right)) 
        return _visit
    visitAdd = _visitBinOperator(_ast.Add)
    visitDiv = _visitBinOperator(_ast.Div)
    visitFloorDiv = _visitBinOperator(_ast.FloorDiv)
    visitLeftShift = _visitBinOperator(_ast.LShift)
    visitMod = _visitBinOperator(_ast.Mod)
    visitMul = _visitBinOperator(_ast.Mult)
    visitPower = _visitBinOperator(_ast.Pow)
    visitRightShift = _visitBinOperator(_ast.RShift)
    visitSub = _visitBinOperator(_ast.Sub)
    del _visitBinOperator

    def _visitBitOperator(opcls):
        def _visit(self, node):
            def _make(nodes):
                if len(nodes) == 1:
                    return self.visit(nodes[0])
                left = _make(nodes[:-1])
                right = self.visit(nodes[-1])
                return self._new(_ast.BinOp, left, opcls(), right)
            return _make(node.nodes)
        return _visit
    visitBitand = _visitBitOperator(_ast.BitAnd)
    visitBitor = _visitBitOperator(_ast.BitOr)
    visitBitxor = _visitBitOperator(_ast.BitXor)
    del _visitBitOperator

    def _visitUnaryOperator(opcls):
        def _visit(self, node):
            return self._new(_ast.UnaryOp, opcls(), self.visit(node.expr))
        return _visit

    visitInvert = _visitUnaryOperator(_ast.Invert)
    visitNot = _visitUnaryOperator(_ast.Not)
    visitUnaryAdd = _visitUnaryOperator(_ast.UAdd)
    visitUnarySub = _visitUnaryOperator(_ast.USub)
    del _visitUnaryOperator

    def _visitBoolOperator(opcls):
        def _visit(self, node):
            values = [self.visit(n) for n in node.nodes]
            return self._new(_ast.BoolOp, opcls(), values)
        return _visit
    visitAnd = _visitBoolOperator(_ast.And)
    visitOr = _visitBoolOperator(_ast.Or)
    del _visitBoolOperator

    cmp_operators = {
        '==': _ast.Eq,
        '!=': _ast.NotEq,
        '<': _ast.Lt,
        '<=': _ast.LtE,
        '>': _ast.Gt,
        '>=': _ast.GtE,
        'is': _ast.Is,
        'is not': _ast.IsNot,
        'in': _ast.In,
        'not in': _ast.NotIn,
    }

    def visitCompare(self, node):
        left = self.visit(node.expr)
        ops = []
        comparators = []
        for optype, expr in node.ops:
            ops.append(self.cmp_operators[optype]())
            comparators.append(self.visit(expr))
        return self._new(_ast.Compare, left, ops, comparators)

    def visitLambda(self, node):
        args = self._extract_args(node)
        body = self.visit(node.code)
        return self._new(_ast.Lambda, args, body)

    def visitIfExp(self, node):
        return self._new(_ast.IfExp, self.visit(node.test), self.visit(node.then),
                        self.visit(node.else_))

    def visitDict(self, node):
        keys = [self.visit(x[0]) for x in node.items]
        values = [self.visit(x[1]) for x in node.items]
        return self._new(_ast.Dict, keys, values)

    def visitListComp(self, node):
        generators = [self.visit(q) for q in node.quals]
        return self._new(_ast.ListComp, self.visit(node.expr), generators)

    def visitGenExprInner(self, node):
        generators = [self.visit(q) for q in node.quals]
        return self._new(_ast.GeneratorExp, self.visit(node.expr), generators)

    def visitGenExpr(self, node):
        return self.visit(node.code)

    def visitGenExprFor(self, node):
        ifs = [self.visit(i) for i in node.ifs]
        return self._new(_ast.comprehension, self.visit(node.assign),
                        self.visit(node.iter), ifs)

    def visitListCompFor(self, node):
        ifs = [self.visit(i) for i in node.ifs]
        return self._new(_ast.comprehension, self.visit(node.assign),
                        self.visit(node.list), ifs)

    def visitGenExprIf(self, node):
        return self.visit(node.test)
    visitListCompIf = visitGenExprIf

    def visitYield(self, node):
        return self._new(_ast.Yield, self.visit(node.value))

    def visitCallFunc(self, node):
        args = []
        keywords = []
        for arg in node.args:
            if isinstance(arg, compiler.ast.Keyword):
                keywords.append(self._new(_ast.keyword, arg.name, 
                                        self.visit(arg.expr)))
            else:
                args.append(self.visit(arg))
        return self._new(_ast.Call, self.visit(node.node), args, keywords,
                    self.visit(node.star_args), self.visit(node.dstar_args))

    def visitBackquote(self, node):
        return self._new(_ast.Repr, self.visit(node.expr))

    def visitConst(self, node):
        if node.value is None: # appears in slices
            return None
        elif isinstance(node.value, (str, unicode,)):
            return self._new(_ast.Str, node.value)
        else:
            return self._new(_ast.Num, node.value)

    def visitName(self, node):
        return self._new(_ast.Name, node.name, _ast.Load())

    def visitGetattr(self, node):
        return self._new(_ast.Attribute, self.visit(node.expr), node.attrname,
                        _ast.Load())

    def visitTuple(self, node):
        nodes = [self.visit(n) for n in node.nodes]
        return self._new(_ast.Tuple, nodes, _ast.Load())

    def visitList(self, node):
        nodes = [self.visit(n) for n in node.nodes]
        return self._new(_ast.List, nodes, _ast.Load())

    def get_ctx(self, flags):
        if flags == 'OP_DELETE':
            return _ast.Del()
        elif flags == 'OP_APPLY':
            return _ast.Load()
        elif flags == 'OP_ASSIGN':
            return _ast.Store()
        else:
            # FIXME Exception here
            assert False, repr(flags)

    def visitAssName(self, node):
        self.out_flags = node.flags
        ctx = self.get_ctx(node.flags)
        return self._new(_ast.Name, node.name, ctx)

    def visitAssAttr(self, node):
        self.out_flags = node.flags
        ctx = self.get_ctx(node.flags)
        return self._new(_ast.Attribute, self.visit(node.expr), 
                         node.attrname, ctx)

    def _visitAssCollection(cls):
        def _visit(self, node):
            flags = None
            elts = []
            for n in node.nodes:
                elts.append(self.visit(n))
                if flags is None:
                    flags = self.out_flags
                else:
                    assert flags == self.out_flags
            self.out_flags = flags
            ctx = self.get_ctx(flags)
            return self._new(cls, elts, ctx)
        return _visit

    visitAssList = _visitAssCollection(_ast.List)
    visitAssTuple = _visitAssCollection(_ast.Tuple)
    del _visitAssCollection

    def visitSlice(self, node):
        lower = self.visit(node.lower)
        upper = self.visit(node.upper)
        ctx = self.get_ctx(node.flags)
        self.out_flags = node.flags
        return self._new(_ast.Subscript, self.visit(node.expr),
                    self._new(_ast.Slice, lower, upper, None), ctx)

    def visitSubscript(self, node):
        ctx = self.get_ctx(node.flags)
        subs = [self.visit(s) for s in node.subs]

        advanced = (_ast.Slice, _ast.Ellipsis)
        slices = []
        nonindex = False
        for sub in subs:
            if isinstance(sub, advanced):
                nonindex = True
                slices.append(sub)
            else:
                slices.append(self._new(_ast.Index, sub))
        if len(slices) == 1:
            slice = slices[0]
        elif nonindex:
            slice = self._new(_ast.ExtSlice, slices)
        else:
            slice = self._new(_ast.Tuple, slices, _ast.Load())

        self.out_flags = node.flags
        return self._new(_ast.Subscript, self.visit(node.expr), slice, ctx)

    def visitSliceobj(self, node):
        a = node.nodes + [None]*(3 - len(node.nodes))
        a = map(self.visit, a)
        return self._new(_ast.Slice, a[0], a[1], a[2])

    def visitEllipsis(self, node):
        return self._new(_ast.Ellipsis)

    def visitStmt(self, node):
        def _check_del(n):
            # del x is just AssName('x', 'OP_DELETE')
            # we want to transform it to Delete([Name('x', Del())])
            dcls = (_ast.Name, _ast.List, _ast.Subscript, _ast.Attribute)
            if isinstance(n, dcls) and isinstance(n.ctx, _ast.Del):
                return self._new(_ast.Delete, [n])
            elif isinstance(n, _ast.Tuple) and isinstance(n.ctx, _ast.Del):
                # unpack last tuple to avoid making del (x, y, z,);
                # out of del x, y, z; (there's no difference between
                # this two in compiler.ast)
                return self._new(_ast.Delete, n.elts)
            else:
                return n
        def _keep(n):
            if isinstance(n, _ast.Expr) and n.value is None:
                return False
            else:
                return True
        statements = [_check_del(self.visit(n)) for n in node.nodes]
        return filter(_keep, statements)


def parse(source, mode):
    node = compiler.parse(source, mode)
    return ASTUpgrader().visit(node)
