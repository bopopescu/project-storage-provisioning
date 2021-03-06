#!/usr/bin/env python
import sys
import os 
from datetime import datetime 
from argparse import ArgumentParser

# adding PYTHONPATH for access to utility modules and 3rd-party libraries
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/../external/lib/python')
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/../')
from utils.Common import getConfig, getMyLogger
from utils.IMailer import SMTPMailer
from utils.IStorage import StorageType, createProjectDirectory
from utils.IProjectDB import getDBConnectInfo, setProjectRoleConfigActions, getProjectRoleConfigActions, updateProjectDatabase, getProjectOwner
from utils.acl.Nfs4NetApp import Nfs4NetApp
from utils.acl.UserRole import ROLE_ADMIN, ROLE_CONTRIBUTOR, ROLE_USER

# execute the main program
if __name__ == "__main__":

    # load configuration file
    cfg  = getConfig( os.path.dirname(os.path.abspath(__file__)) + '/../etc/config.ini' )

    parg = ArgumentParser(description='activates project roles settings pending in the ProjectDB', version="0.1")

    # optional arguments
    parg.add_argument('-l','--loglevel',
                      action  = 'store',
                      dest    = 'verbose',
                      type    = int,
                      choices = [0, 1, 2, 3],
                      default = 0,
                      help    = 'set one of the following verbosity levels. 0|default:WARNING, 1:ERROR, 2:INFO, 3:DEBUG')

    parg.add_argument('-d','--basedir',
                      action  = 'store',
                      dest    = 'basedir',
                      default = cfg.get('PPS','PROJECT_BASEDIR'),
                      help    = 'set the basedir in which the project storages are located')

    parg.add_argument('-f','--force',
                      action  = 'store_true',
                      dest    = 'force',
                      default = False,
                      help    = 'force updating the ACL even the user is already in the given role, useful for fixing ACL table')

    parg.add_argument('-t','--test',
                      action  = 'store_true',
                      dest    = 'do_test',
                      default = False,
                      help    = 'perform a test run, i.e. volume creation on central storage and ACL setting are ignored')

    parg.add_argument('-c','--mkdir',
                      action  = 'store_true',
                      dest    = 'do_mkdir',
                      default = False,
                      help    = 'create project directory with mkdir instead of adding new filer volume for new project')

    args = parg.parse_args()

    logger = getMyLogger(name=os.path.basename(__file__), lvl=args.verbose)

    # project database connection information
    (db_host, db_uid, db_name, db_pass) = getDBConnectInfo(cfg)
    
    # retrieve pending actions
    actions = getProjectRoleConfigActions(db_host, db_uid, db_pass, db_name, lvl=args.verbose)

    if not actions:
        # break the program when no pending actions
        logger.warn('I have nothing to do!')
        sys.exit(0) 

    # re-org actions in projects so that we can perform actions by project
    prjs = list( set(map(lambda x:x.pid, actions)) )


    fs = Nfs4NetApp('', lvl=args.verbose)
    for pid in prjs:

        p_actions = filter(lambda x:x.pid==pid, actions)

        logger.info('performing actions on project: %s' % pid)

        p_dir = os.path.join(args.basedir, pid)

        # create project directory if not available
        isInit = False
        if not os.path.exists( p_dir ):

            rc    = True
            stype = ''
            quota = '%sGB' % p_actions[0].pquota
            if args.do_mkdir:
                stype = 'fs_dir'
            else:
                if args.do_test:
                    # in test mode, we create a local directory
                    stype = 'fs_dir'
                else:
                    stype = 'netapp_volume'

            logger.info('  |- creating project directory as %s' % stype)

            rc = createProjectDirectory(p_dir, quota, StorageType[stype], cfg, lvl=args.verbose)

            if not rc:
                logger.error('failed to create directory for project: %s' % pid)
                continue
            else:
                # refresh the PROJECT_BASEDIR to get access to the newly created volume
                os.listdir(cfg.get('PPS','PROJECT_BASEDIR'))
                if not os.path.exists( p_dir ):
                    logger.error('created directory not available: %s' % p_dir)
                    continue
                else:
                    isInit = True
                    
        # perform set ACL action
        logger.info('  |-> performing set ACL on project: %s' % pid)
        _set_a     = filter(lambda x:x.action=='set' , p_actions)
        _l_admin   = map(lambda x:x.uid, filter(lambda x:x.role==ROLE_ADMIN      , _set_a))
        _l_user    = map(lambda x:x.uid, filter(lambda x:x.role==ROLE_USER       , _set_a))
        _l_contrib = map(lambda x:x.uid, filter(lambda x:x.role==ROLE_CONTRIBUTOR, _set_a))

        logger.info('  |- set %s role: %s' % (ROLE_ADMIN, repr(_l_admin)))
        logger.info('  |- set %s role: %s' % (ROLE_CONTRIBUTOR, repr(_l_contrib)))
        logger.info('  |- set %s role: %s' % (ROLE_USER, repr(_l_user)))

        rc = True
        fs.project_root = p_dir
        if not args.do_test:
            # while initializing the project's ACL, there is no need to set ACL for sub-directories.
            # therefore, the first two arguments of setACE are the same and equal to the project's top directory.
            rc = fs.setRoles(users=_l_user, contributors=_l_contrib, admins=_l_admin, force=args.force, traverse=False)

        if rc:
            for a in _set_a:
                a.atime = datetime.now()

        # perform del ACL action
        logger.info('  |- performing del ACL on project: %s' % pid)
        _del_a = filter(lambda x: x.action == 'delete', p_actions)
        _l_user = map(lambda x: x.uid, _del_a)

        logger.info('  |- del user(s): %s' % repr(_l_user))

        rc = True
        if not args.do_test:
            # while initializing the project's ACL, there is no need to set ACL for sub-directories.
            # therefore, the first two arguments of delACE are the same and equal to the project's top directory.
            rc = fs.delUsers(users=_l_user, force=args.force)

        if rc:
            for a in _del_a:
                a.atime = datetime.now()

        # update project database on activate roles for this project
        setProjectRoleConfigActions(db_host, db_uid, db_pass, db_name, actions=filter(lambda x: x.atime, actions), lvl=args.verbose)

        # retrieve the up-to-date user roles for this project
        roles = {pid: fs.getRoles(recursive=False)}

        # updating project DB database with the currently activated user roles
        updateProjectDatabase(roles, db_host, db_uid, db_pass, db_name, lvl=args.verbose)
        
        # send email to project owner if it's a creation of the project
        # - get project owner email
        # - compose html email (if isInit is True, notify user the project storage is created)
        # - send via service email account
        if isInit:
            owner = getProjectOwner(db_host, db_uid, db_pass, db_name, pid, lvl=args.verbose)

            if owner and owner['email']:
                smtp_host = cfg.get('MAILER','SMTP_HOST')
                smtp_port = cfg.get('MAILER','SMTP_PORT')
                smtp_user = cfg.get('MAILER','SMTP_USERNAME')
                smtp_pass = cfg.get('MAILER','SMTP_PASSWORD')

                smtp_credential = None
                if smtp_user and smtp_pass:
                    smtp_credential = {'username': smtp_user, 'password': smtp_pass}

                mailer = SMTPMailer(host=smtp_host, port=smtp_port, credential=smtp_credential, lvl=args.verbose)

                subject = 'Storage of your project %s initialised' % pid
                toAddress = '%s <%s>' % ( owner['name'], owner['email'] )

                # content of the email
                _parts = {'plain':cfg.get('MAILER','EMAIL_TEMPLATE_PROJECT_INIT')}
                _parts['plain'] = _parts['plain'].replace('@@NL@@','').replace('@@PROJECTOWNER@@', owner['name']).replace('@@PROJECTID@@', pid)

                # send email
                mailer.sendMultipartEmail(subject=subject, fromAddress=cfg.get('MAILER','EMAIL_FROM_ADDRESS'), toAddress=toAddress, parts=_parts)

            else:
                logger.warn('project owner (email) unknown: %s %s' % (pid, repr(owner)))
