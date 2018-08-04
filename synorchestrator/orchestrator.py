#!/usr/bin/env python
"""
Takes a given ID/URL for a workflow registered in a given TRS
implementation; prepare the workflow run request, including
retrieval and formatting of parameters, if not provided; post
the workflow run request to a given WES implementation;
monitor and report results of the workflow run.
"""
import logging
import sys
import time
import re
import os
import datetime as dt
import subprocess
from IPython.display import display, clear_output
from synorchestrator import config
from synorchestrator.util import get_json, get_packed_cwl, ctime2datetime, convert_timedelta
from synorchestrator.trs.client import TRSClient
from synorchestrator.wes.client import WESClient
from synorchestrator.eval import (create_submission,
                                  get_submission_bundle,
                                  get_submissions,
                                  update_submission_run,
                                  update_submission_status,
                                  submission_queue)
from wes_client.util import build_wes_request

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def run_submission(wes_id, submission_id):
    """
    For a single submission to a single evaluation queue, run
    the workflow in a single environment.
    """
    submission = get_submission_bundle(wes_id, submission_id)

    logger.info("Submitting to WES endpoint '{}':"
                "\n - submission ID: {}"
                .format(wes_id, submission_id))

    client = WESClient(config.wes_config()[wes_id])
    run_data = client.run_workflow(submission['data']['wf'],
                                   submission['data']['jsonyaml'],
                                   submission['data']['attachments'])
    run_data['start_time'] = dt.datetime.now().ctime()
    update_submission_run(wes_id, submission_id, run_data)
    update_submission_status(wes_id, submission_id, 'SUBMITTED')
    return run_data


def run_next_queued(wes_id):
    """
    Run the next submission slated for a single WES endpoint.

    Return None if no submissions are queued.
    """
    queued_submissions = get_submissions(wes_id, status='RECEIVED')
    if not queued_submissions:
        return None
    for submission_id in sorted(queued_submissions):
        return run_submission(wes_id, submission_id)


# def run_checker(eval_id, wes_id, queue_only=True):
#     """
#     Run checker workflow for an evaluation workflow in a single
#     environment.
#     """
#     workflow_config = config.eval_config()[eval_id]
#     workflow_config['id'] = workflow_config['workflow_id']
#
#     logger.info("Preparing checker workflow run request for '{}' from '{}''"
#                 .format(workflow_config['id'], workflow_config['trs_id']))
#
#     client = TRSClient(**config.trs_config()[workflow_config['trs_id']])
#     checker_workflow = client.get_workflow_checker(workflow_config['id'])
#     checker_descriptor = client.get_workflow_descriptor(
#         id=checker_workflow['id'],
#         version_id=workflow_config['version_id'],
#         type=workflow_config['workflow_type']
#     )
#     if checker_descriptor['type'] == 'CWL' and re.search('run:', checker_descriptor['descriptor']):
#         checker_descriptor['descriptor'] = get_packed_cwl(checker_descriptor['url'])
#
#     checker_tests = client.get_workflow_tests(
#         fileid=checker_workflow['id'],
#         version_id=workflow_config['version_id'],
#         filetype=workflow_config['workflow_type']
#     )
#     wes_request = build_wes_request(checker_descriptor['descriptor'],
#                                     checker_tests[0]['url'])
#     submission_id = create_submission(eval_id, wes_request, wes_id)
#     if not queue_only:
#         return run_next_queued(eval_id, wes_id)
#     else:
#         return submission_id


def monitor(submissions):
    """
    Monitor progress of workflow jobs.
    """
    import pandas as pd
    pd.set_option('display.width', 100)

    current = dt.datetime.now()
    statuses = []
    # for local, toil, cromwell, arvados, cwltool, etc.
    for wf_service in submissions:
        status_dict = {}
        # for run ID######## in each
        for run_id in submissions[wf_service]:
            if 'run' not in submissions[wf_service][run_id]:
                continue
            run = submissions[wf_service][run_id]['run']
            client = WESClient(config.wes_config()[wf_service])
            # updated_status = client.get_workflow_run_status(run['run_id'])
            updated_status = 'RUNNING'
            run['state'] = updated_status
            if run['state'] in ['QUEUED', 'INITIALIZING', 'RUNNING']:
                etime = convert_timedelta(current - ctime2datetime(run['start_time']))
            elif 'elapsed_time' not in run:
                etime = 0
            else:
                etime = run['elapsed_time']
            status_dict.setdefault(wf_service, {})[run_id] = {
                'wf_id': submissions[wf_service][run_id]['wf_id'],
                'run_status': updated_status,
                'wes_id': wf_service,
                'start_time': run['start_time'],
                'elapsed_time': etime
            }
        statuses.append(status_dict)

    status_df = pd.DataFrame.from_dict(
        {(i, j): status[i][j]
         for status in statuses
         for i in status.keys()
         for j in status[i].keys()},
        orient='index'
    )

    clear_output(wait=True)
    os.system('clear')
    display(status_df)
    sys.stdout.flush()
    if any(status_df['run_status'].isin(['QUEUED', 'INITIALIZING', 'RUNNING'])):
        time.sleep(1)
        monitor(get_json(submission_queue))
    else:
        print("Done!")


# create_submission(wes_id='local',
#                   submission_data={'wf': '/home/quokka/git/workflow-service/testdata/md5sum.cwl',
#                                    'jsonyaml': 'file:///home/quokka/git/workflow-service/testdata/md5sum.cwl.json',
#                                    'attachments': ['file:///home/quokka/git/workflow-service/testdata/md5sum.input',
#                                                    'file:///home/quokka/git/workflow-service/testdata/dockstore-tool-md5sum.cwl']},
#                   wf_name='wflow0',
#                   type='cwl')
#
# print(get_submission_bundle("local", "040804130201818647"))
print(run_submission("local", "040804130201818647"))
i = get_submissions("local", status='RECEIVED')
print(i)
j = get_json(submission_queue)
monitor(j)
