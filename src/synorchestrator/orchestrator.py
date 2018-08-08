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
import os
import datetime as dt
from requests.exceptions import ConnectionError
from IPython.display import display, clear_output
from wes_client.util import get_status

from synorchestrator.config import wes_config, wf_config
from synorchestrator.util import ctime2datetime, convert_timedelta
from synorchestrator.wes.client import WESClient
from synorchestrator.util import get_json, save_json

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


QUEUE_PATH = os.path.join(os.path.dirname(__file__), 'config_files', 'submission_queue.json')


def create_submission(wes_id, submission_data, wf_type, wf_name, sample):
    """
    Submit a new job request to an evaluation queue.

    Both type and wf_name are optional but could be used with TRS.
    """
    submissions = get_json(QUEUE_PATH)
    submission_id = dt.datetime.now().strftime('%d%m%d%H%M%S%f')

    submissions.setdefault(wes_id, {})[submission_id] = {'status': 'RECEIVED',
                                                         'data': submission_data,
                                                         'wf_id': wf_name,
                                                         'type': wf_type,
                                                         'sample': sample}
    save_json(QUEUE_PATH, submissions)
    logger.info(" Queueing Job for '{}' endpoint:"
                "\n - submission ID: {}".format(wes_id, submission_id))
    return submission_id


def get_submissions(wes_id, status='RECEIVED'):
    """Return all ids with the requested status."""
    submissions = get_json(QUEUE_PATH)
    return [id for id, bundle in submissions[wes_id].items() if bundle['status'] == status]


def get_submission_bundle(wes_id, submission_id):
    """Return the submission's info."""
    return get_json(QUEUE_PATH)[wes_id][submission_id]


def update_submission(wes_id, submission_id, param, status):
    """Update the status of a submission."""
    submissions = get_json(QUEUE_PATH)
    submissions[wes_id][submission_id][param] = status
    save_json(QUEUE_PATH, submissions)


def update_submission_run(wes_id, submission_id, param, status):
    """Update the status of a submission."""
    submissions = get_json(QUEUE_PATH)
    submissions[wes_id][submission_id]['run'][param] = status
    save_json(QUEUE_PATH, submissions)


def set_queue_from_user_json(filepath):
    # TODO verify terms match between configs
    sdict = get_json(filepath)
    for wf_service in sdict:
        for sample in sdict[wf_service]:
            wf_name = sdict[wf_service][sample]['wf_name']
            wf_jsonyaml = sdict[wf_service][sample]['jsonyaml']
            print('Queueing "{}" on "{}" with data: {}'.format(wf_name, wf_service, sample))
            queue(wf_service, wf_name, wf_jsonyaml)


def queue(service, wf_name, wf_jsonyaml, sample='NA', attach=None):
    """
    Put a workflow in the queue.

    :param service:
    :param wf_name:
    :param wf_jsonyaml:
    :param sample:
    :param attach:
    :return:
    """
    # fetch workflow params from config file
    # synorchestrator.config.add_workflow() can be used to add a workflow to this file
    wf = wf_config()[wf_name]

    if not attach:
        attach = wf['workflow_attachments']

    submission_id = create_submission(wes_id=service,
                                      submission_data={'wf': wf['workflow_url'],
                                                       'jsonyaml': wf_jsonyaml,
                                                       'attachments': attach},
                                      wf_name=wf_name,
                                      wf_type=wf['workflow_type'],
                                      sample=sample)
    return submission_id


def no_queue_run(service, wf_name, wf_jsonyaml, sample='NA', attach=None):
    """
    Put a workflow in the queue and immmediately run it.

    :param service:
    :param wf_name:
    :param wf_jsonyaml:
    :param sample:
    :param attach:
    :return:
    """
    submission_id = queue(service, wf_name, wf_jsonyaml, sample=sample, attach=attach)
    run_submission(service, submission_id)


def run_submission(wes_id, submission_id):
    """
    For a single submission to a single evaluation queue, run
    the workflow in a single environment.
    """
    submission = get_submission_bundle(wes_id, submission_id)

    logger.info(" Submitting to WES endpoint '{}':"
                " \n - submission ID: {}"
                .format(wes_id, submission_id))

    client = WESClient(wes_config()[wes_id])
    run_data = client.run_workflow(submission['data']['wf'],
                                   submission['data']['jsonyaml'],
                                   submission['data']['attachments'])
    run_data['start_time'] = dt.datetime.now().ctime()
    update_submission(wes_id, submission_id, 'run', run_data)
    update_submission(wes_id, submission_id, 'status', 'SUBMITTED')
    return run_data


def run_next_queued(wf_service):
    """
    Run the next submission slated for a single WES endpoint.

    Return None if no submissions are queued.
    """
    queued_submissions = get_submissions(wf_service, status='RECEIVED')
    if not queued_submissions:
        return False
    for submssn_id in sorted(queued_submissions):
        return run_submission(wf_service, submssn_id)


def run_all():
    """
    Run all jobs with the status: RECEIVED in the submission queue.

    Check the status of each job per workflow service for status: COMPLETE
    before running the next queued job.
    """
    # create a dictionary of services
    current_job_state = {}
    for wf_service in wes_config():
        current_job_state[wf_service] = ''

    # check all wfs for a given service for RUNNING/INITing/SUBMITTED (skip if True)
    # else run the first queue
    for wf_service in wes_config():
        submissions_left = True
        while submissions_left:
            submissions_left = run_next_queued(wf_service)
            if not submissions_left:
                break
            status = get_status(submissions_left['run_id'])
            while status != 'COMPLETE':
                time.sleep(4)


def monitor_service(wf_service):
    """
    Returns a dictionary of all of the jobs under a single wes service appropriate
    for displaying as a pandas dataframe.

    :param wf_service:
    :return:
    """
    status_dict = {}
    submissions = get_json(QUEUE_PATH)
    for run_id in submissions[wf_service]:
        sample_name = submissions[wf_service][run_id]['sample']
        if 'run' not in submissions[wf_service][run_id]:
            status_dict.setdefault(wf_service, {})[run_id] = {
                'wf_id': submissions[wf_service][run_id]['wf_id'],
                'run_id': '-',
                'sample_name': sample_name,
                'run_status': 'QUEUED',
                'start_time': '-',
                'elapsed_time': '-'}
        else:
            try:
                run = submissions[wf_service][run_id]['run']

                client = WESClient(wes_config()[wf_service])
                run['state'] = client.get_workflow_run_status(run['run_id'])['state']
                if run['state'] in ['QUEUED', 'INITIALIZING', 'RUNNING']:
                    etime = convert_timedelta(dt.datetime.now() - ctime2datetime(run['start_time']))
                elif 'elapsed_time' not in run:
                    etime = '0h:0m:0s'
                else:
                    etime = run['elapsed_time']
                update_submission_run(wf_service, run_id, 'elapsed_time', etime)
                status_dict.setdefault(wf_service, {})[run_id] = {
                    'wf_id': submissions[wf_service][run_id]['wf_id'],
                    'run_id': run['run_id'],
                    'sample_name': sample_name,
                    'run_status': run['state'],
                    'start_time': run['start_time'],
                    'elapsed_time': etime}
            except ConnectionError:
                status_dict.setdefault(wf_service, {})[run_id] = {
                    'wf_id': 'ConnectionError',
                    'run_id': '-',
                    'sample_name': sample_name,
                    'run_status': '-',
                    'start_time': '-',
                    'elapsed_time': '-'}

    return status_dict


def monitor():
    """Monitor progress of workflow jobs."""
    import pandas as pd
    pd.set_option('display.width', 100)

    while True:
        statuses = []
        submissions = get_json(QUEUE_PATH)

        for wf_service in submissions:
            statuses.append(monitor_service(wf_service))

        status_df = pd.DataFrame.from_dict(
            {(i, j): status[i][j]
             for status in statuses
             for i in status.keys()
             for j in status[i].keys()},
            orient='index')

        clear_output(wait=True)
        os.system('clear')
        display(status_df)
        sys.stdout.flush()
        time.sleep(1)