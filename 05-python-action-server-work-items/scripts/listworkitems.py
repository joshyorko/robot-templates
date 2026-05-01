from robocorp.tasks import task
from actions import workitems


@task
def list_work_items():
    for item in workitems.inputs:
        payload = item.payload
        print(payload)


