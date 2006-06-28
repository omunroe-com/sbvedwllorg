# -*- coding: utf-8 -*-
#
# Copyright (C) 2006 Christopher Lenz
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://markup.cmlenz.net/wiki/License.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://markup.cmlenz.net/log/.

import doctest
import unittest

from markup.core import Stream
from markup.input import XMLParser


def suite():
    suite = unittest.TestSuite()
    suite.addTest(doctest.DocTestSuite(XMLParser.__module__))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
