#!/usr/bin/env python3
# Adam Bowen - Apr 2016
# This script refreshes a vdb
# Updated by Corey Brune Oct 2016
# requirements
# pip install --upgrade setuptools pip docopt delphixpy

# The below doc follows the POSIX compliant standards and allows us to use
# this doc to also define our ARGUMENTS for the script.
"""Refresh a vdb
Usage:
  dx_refresh_vdb.py --vdb <name>
  [--timestamp_type <type> --timestamp <timepoint_semantic> ]
  [--timeflow <timeflow> -d <identifier> | --engine <identifier> | --all]
  [--debug] [--parallel <n>] [--poll <n>] [--single_thread <bool>]
  [--config <path_to_file>] [--logdir <path_to_file>]
  dx_refresh_vdb.py -h | --help | -v | --version
Refresh a Delphix VDB
Examples:
  dx_refresh_vdb.py --vdb "aseTest"
  dx_refresh_vdb.py --all_vdbs --host LINUXSOURCE -d landsharkengine
  dx_refresh_vdb.py --all_vdbs --group_name "Analytics" --all
Options:
  --vdb <name>              Name of the object you are refreshing.
  --all_vdbs                Refresh all VDBs that meet the filter criteria.
  --group_name <name>       Name of the group to execute against.
  --single_thread           Run as a single thread. False if running multiple
                            threads.
                            [default: True]
  --host <name>             Name of environment in Delphix to execute against.
  --timestamp_type <type>   The type of timestamp you are specifying.
                            Acceptable Values: TIME, SNAPSHOT
                            [default: SNAPSHOT]
  --timestamp <timepoint_semantic>
                            The Delphix semantic for the point in time on
                            the source from which you want to refresh your VDB.
                            Formats:
                            latest point in time or snapshot: LATEST
                            point in time: "YYYY-MM-DD HH24:MI:SS"
                            snapshot name: "@YYYY-MM-DDTHH24:MI:SS.ZZZ"
                            snapshot time from GUI: "YYYY-MM-DD HH24:MI"
                            [default: LATEST]
  --timeflow <name>         Name of the timeflow to refresh a VDB
  -d <identifier>           Identifier of Delphix engine in dxtools.conf.
  --engine <name>           Alt Identifier of Delphix DDP in dxtools.conf.
                              [default: default]
  --all                     Run against all engines.
  --debug                   Enable debug logging
  --parallel <n>            Limit number of jobs to maxjob
  --poll <n>                The number of seconds to wait between job polls
                            [default: 10]
  --config <path_to_file>   The path to the dxtools.conf file
                            [default: ./dxtools.conf]
  --logdir <path_to_file>   The path to the logfile you want to use.
                            [default: ./dx_refresh_db.log]
  -h --help                 Show this screen.
  -v --version              Show version.
"""

from os.path import basename
import sys
import time
import docopt

from delphixpy.v1_10_2 import exceptions
from delphixpy.v1_10_2.web import database
from delphixpy.v1_10_2.web import vo

from lib import dlpx_exceptions
from lib import dx_timeflow
from lib import get_session
from lib import get_references
from lib import dx_logging
from lib import run_job
from lib.run_async import run_async

VERSION = 'v.0.3.001'


def refresh_vdb(dlpx_obj, vdb_name, timestamp, timestamp_type='SNAPSHOT'):
    """
    This function actually performs the refresh
    engine:
    :param dlpx_obj: DDP session object
    :type dlpx_obj: lib.GetSession.GetSession object
    :param vdb_name: VDB to be refreshed
    :type vdb_name: str
    :param timestamp: The Delphix semantic for the point in time on the
        source from which you want to refresh your VDB
    :type timestamp: str
    :param timestamp_type: either SNAPSHOT or TIME
    :type timestamp_type: str
    """
    dx_timeflow_obj = dx_timeflow.DxTimeflow(dlpx_obj.server_session)
    container_obj = get_references.find_obj_by_name(
        dlpx_obj.server_session, database, vdb_name)
    source_obj = get_references.find_source_by_db_name(
        dlpx_obj.server_session, vdb_name)
    # Sanity check to make sure our container object has a reference
    if container_obj.reference:
        try:
            if container_obj.virtual is not True or \
                    container_obj.staging is True:
                dx_logging.print_exception(
                    f'{container_obj.name} is not a virtual object.\n')
            elif container_obj.runtime.enabled == 'ENABLED':
                dx_logging.print_info(f'INFO: Refreshing {container_obj.name} '
                                      f'to {timestamp}\n')
        # This exception is raised if refreshing a vFiles VDB since
        # AppDataContainer does not have virtual, staging or enabled attributes
        except AttributeError:
            pass
    if source_obj.reference:
        try:
            source_db = database.get(dlpx_obj.server_session,
                                     container_obj.provision_container)
        except (exceptions.RequestError, exceptions.JobError) as err:
            raise dlpx_exceptions.DlpxException(
                f'Encountered error while refreshing {vdb_name}:\n{err}')
        rewind_params = vo.RollbackParameters()
        rewind_params.timeflow_point_parameters = \
            dx_timeflow_obj.set_timeflow_point(container_obj, timestamp_type,
                                               timestamp)
        if str(container_obj.reference).startswith("ORACLE"):
            refresh_params = vo.OracleRefreshParameters()
        else:
            refresh_params = vo.RefreshParameters()
        try:
            refresh_params.timeflow_point_parameters = \
                dx_timeflow_obj.set_timeflow_point(source_db, timestamp_type,
                                                   timestamp)
            database.refresh(dlpx_obj.server_session, container_obj.reference,
                             refresh_params)
            dlpx_obj.jobs[dlpx_obj.server_session.address] = \
                dlpx_obj.server_session.last_job
        except (dlpx_exceptions.DlpxException, exceptions.RequestError) as err:
            raise dlpx_exceptions.DlpxObjectNotFound(
                f'ERROR: Could not set timeflow point:\n{err}')
        # Don't do anything if the database is disabled
        else:
            dx_logging.print_info(f'INFO: {container_obj.name} is not '
                                  f'enabled. Skipping sync.\n')


@run_async
def main_workflow(engine, dlpx_obj, single_thread):
    """
    This function is where we create our main workflow.
    Use the @run_async decorator to run this function asynchronously.
    The @run_async decorator allows us to run against multiple Delphix Engine
    simultaneously
    :param engine: Dictionary of engines
    :type engine: dictionary
    :param dlpx_obj: DDP session object
    :type dlpx_obj: lib.GetSession.GetSession object
    :param single_thread: True - run single threaded, False - run multi-thread
    :type single_thread: bool
    """
    try:
        # Setup the connection to the Delphix DDP
        dlpx_obj.dlpx_session(engine['ip_address'], engine['username'],
                              engine['password'])
    except dlpx_exceptions.DlpxException as err:
        dx_logging.print_exception(
            f'ERROR: dx_refresh_vdb encountered an error authenticating to '
            f'{engine["hostname"]} {ARGUMENTS["--target"]}:\n{err}\n')
    thingstodo = ['thingstodo']
    try:
        with dlpx_obj.job_mode(single_thread):
            while dlpx_obj.jobs or thingstodo:
                if thingstodo:
                    refresh_vdb(
                        dlpx_obj, ARGUMENTS['--vdb'], ARGUMENTS['--timestamp'],
                        ARGUMENTS['--timestamp_type'])
                thingstodo.pop()
    except (dlpx_exceptions.DlpxException, dlpx_exceptions.DlpxObjectNotFound,
            exceptions.RequestError, exceptions.JobError,
            exceptions.HttpError) as err:
        dx_logging.print_exception(f'Error in dx_refresh_vdb:'
                                   f'{engine["hostname"]}\n{err}')
        run_job.find_job_state(engine, dlpx_obj)


def main():
    """
    main function - creates session and runs jobs
    """
    time_start = time.time()
    try:
        dx_session_obj = get_session.GetSession()
        dx_logging.logging_est(ARGUMENTS['--logdir'])
        config_file_path = ARGUMENTS['--config']
        single_thread = ARGUMENTS['--single_thread']
        engine = ARGUMENTS['--engine']
        dx_session_obj.get_config(config_file_path)
        # This is the function that will handle processing main_workflow for
        # all the servers.
        for each in run_job.run_job(main_workflow, dx_session_obj, engine,
                                    single_thread):
            # join them back together so that we wait for all threads to
            # complete
            each.join()
        elapsed_minutes = run_job.time_elapsed(time_start)
        dx_logging.print_info(f'script took {elapsed_minutes} minutes to '
                              f'get this far.')
    # Here we handle what we do when the unexpected happens
    except SystemExit as err:
        # This is what we use to handle our sys.exit(#)
        sys.exit(err)

    except dlpx_exceptions.DlpxException as err:
        # We use this exception handler when an error occurs in a function
        # call.
        dx_logging.print_exception(f'ERROR: Please check the ERROR message '
                                   f'below:\n {err.error}')
        sys.exit(2)

    except exceptions.HttpError as err:
        # We use this exception handler when our connection to Delphix fails
        dx_logging.print_exception(
            f'ERROR: Connection failed to the Delphix DDP. Please check '
            f'the ERROR message below:\n{err.status}')
        sys.exit(2)

    except exceptions.JobError as err:
        # We use this exception handler when a job fails in Delphix so that we
        # have actionable data
        elapsed_minutes = run_job.time_elapsed(time_start)
        dx_logging.print_exception(
            f'A job failed in the Delphix Engine:\n{err.job}.'
            f'{basename(__file__)} took {elapsed_minutes} minutes to get '
            f'this far')
        sys.exit(3)

    except KeyboardInterrupt:
        # We use this exception handler to gracefully handle ctrl+c exits
        dx_logging.print_debug('You sent a CTRL+C to interrupt the process')
        elapsed_minutes = run_job.time_elapsed(time_start)
        dx_logging.print_info(f'{basename(__file__)} took {elapsed_minutes} '
                              f'minutes to get this far.')


if __name__ == "__main__":
    # Grab our ARGUMENTS from the doc at the top of the script
    ARGUMENTS = docopt.docopt(__doc__,
                              version=basename(__file__) + " " + VERSION)
    # Feed our ARGUMENTS to the main function, and off we go!
    main()