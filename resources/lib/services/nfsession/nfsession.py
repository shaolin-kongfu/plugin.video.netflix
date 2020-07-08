# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Stateful Netflix session management

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals

import resources.lib.common as common
from resources.lib.services.nfsession.directorybuilder.dir_builder import DirectoryBuilder
from resources.lib.services.nfsession.nfsession_op import NFSessionOperations


class NetflixSession(DirectoryBuilder):
    """Stateful netflix session management"""

    def __init__(self):
        # Initialize correlated features
        DirectoryBuilder.__init__(self, self.nfsession)
        # Create and establish the Netflix session
        self.nfsession = NFSessionOperations()
        # Register the functions to IPC
        slots = [
            self.nfsession.get_safe,
            self.nfsession.post_safe,
            self.nfsession.login,
            self.nfsession.logout,
            self.nfsession.path_request,
            self.nfsession.perpetual_path_request,
            self.nfsession.callpath_request,
            self.nfsession.fetch_initial_page,
            self.nfsession.activate_profile,
            self.nfsession.parental_control_data
        ]
        for slot in slots:
            common.register_slot(common.EnvelopeIPCReturnCall(slot).call, slot.__name__)
        # Silent login
        self.nfsession.prefetch_login()
