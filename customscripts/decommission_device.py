from configparser import ConfigParser
from datetime import date

from google.oauth2 import service_account
from googleapiclient import discovery

from dcim.models import Device
from extras.scripts import Script, StringVar


GOOGLE_API_CONFIG_FILE = '/etc/netbox/gsheets.cfg'
GOOGLE_SHEET_NAME_FORMAT = 'Decommissioned Devices {year} Q{quarter}'
GOOGLE_DRIVE_PARENT_FOLDER_ID = '181AFOXai6e8SR9E6sadqYiWyE1VdnlPD'
SITES = ['eqiad', 'codfw', 'ulsfo', 'esams', 'eqsin']


class DecommissionDevice(Script):

    class Meta:
        name = 'Decommission a device with extra actions'
        description = 'Set the device status to Decommissioning and update the Decommission Google Spreadsheet.'

    device_name = StringVar(label='Device name', description='Device to be decommissioned')
    decom_task = StringVar(label='Phab Task', description='Decommissioning Phabricator task ID', regex='T[0-9]+')

    def run(self, data):
        """Decommission the device and update the Google Spreadsheet."""
        try:
            message = self._run(data)
            self.log_success(message)
        except Exception as e:
            message = 'Failed to decommission device {name}: {e}'.format(name=data['device_name'], e=e)
            self.log_failure(message)

        return message

    def _run(self, data):
        """Actually run the script."""
        device = Device.objects.get(name=data['device_name'])
        self.log_info('Found Netbox device')
        if device.status in ('Decommissioning', 'Offline'):
            raise RuntimeError('Device {name} is in {status} status already.'.format(
                name=device.name, status=device.status))

        config = ConfigParser()
        config.read(GOOGLE_API_CONFIG_FILE)

        gfiles = discovery.build(
            'drive', 'v3', credentials=self._get_credentials('drive', config['service-credentials'])).files()
        gsheets = discovery.build(
            'sheets', 'v4', credentials=self._get_credentials('spreadsheets', config['service-credentials'])
        ).spreadsheets()

        sheet_name = self._get_sheet_title()
        sheet = self._get_sheet(gfiles, gsheets, sheet_name)
        self._update_sheet(device, gsheets, sheet['id'], data['decom_task'])

        # device.status = 'Decommissioning'
        # device.save()

        return ("Successfully decommissioned device {name} and updated Google Spreadsheet '{sheet_name}': "
                "https://docs.google.com/spreadsheets/d/{sheet_id}").format(
                    name=device.name, sheet_name=sheet_name, sheet_id=sheet['id'])

    def _get_sheet_title(self):
        """Get the Google Spreadsheet title."""
        today = date.today()
        quarter = ((((today.month - 1) // 3) + 2) % 4) + 1  # Wikimedia quarter
        return GOOGLE_SHEET_NAME_FORMAT.format(year=today.year, quarter=quarter)

    def _get_credentials(self, service, credentials):
        """Get Google API credentials for a given service."""
        scope = 'https://www.googleapis.com/auth/' + service
        return service_account.Credentials.from_service_account_info(credentials, scopes=[scope])

    def _get_sheet(self, gfiles, gsheets, name):
        """Get or create the Google Spreadsheet."""
        query = "name = '{name}'".format(name=name)
        self.log_debug('Looking for spreadsheet: {name}'.format(name=name))
        response = gfiles.list(corpora='user', fields='files(id, name)', q=query).execute()
        if not response['files']:
            self.log_info('Spreadsheet not found, creating a new one')
            return self._create_sheet(gfiles, gsheets, name)

        if len(response['files']) > 1:
            self.log_failure('Too many files found: {files}'.format(files=response['files']))
            raise RuntimeError('Found multiple files with same name: {files}'.format(files=response['files']))

        return response['files'][0]

    def _create_sheet(self, gfiles, gsheets, name):
        """Create a new Google Spreadsheet and initialize it."""
        metadata = {'name': name, 'parents': [GOOGLE_DRIVE_PARENT_FOLDER_ID],
                    'mimeType': 'application/vnd.google-apps.spreadsheet'}
        # Create a new empty Spreadsheet
        sheet = gfiles.create(body=metadata).execute()
        self.log_debug('Spreadsheet created')
        # Rename and format rows and columns of the first sheet
        gsheets.batchUpdate(spreadsheetId=sheet['id'], body=self._get_format_request()).execute()
        self.log_debug('First sheet renamed and formatted')
        # Add the header row to the first sheet
        gsheets.values().batchUpdate(spreadsheetId=sheet['id'], body=self._get_sheet_headers()).execute()
        self.log_debug('Inserted header values in the first sheet')
        # Duplicate the first sheet to make one per site
        gsheets.batchUpdate(spreadsheetId=sheet['id'], body=self._get_duplicate_sheet_request()).execute()
        self.log_debug('Duplicated first sheet for each site')

        return sheet

    def _update_sheet(self, device, gsheets, sheet_id, decom_task):
        """Append a row to the sheet with the device data."""
        model = ' '.join([device.device_type.manufacturer.name, device.device_type.model])
        purchase = device.cf['purchase_date']
        expiry = date(year=purchase.year + 3, month=purchase.month, day=purchase.day)
        values = [device.site.slug, device.asset_tag, str(date.today()), device.cf['ticket'], 'No', 'No', device.name,
                  device.serial, decom_task, str(expiry), model]
        body = {'values': [values]}
        gsheets.values().append(
            spreadsheetId=sheet_id, range=device.site.slug, valueInputOption='USER_ENTERED', body=body).execute()
        self.log_info('Appended row with decommissioned host data')

    def _get_duplicate_sheet_request(self):
        sheets = [{'duplicateSheet': {
            'sourceSheetId': 0, 'insertSheetIndex': i + 1, 'newSheetId': i + 1, 'newSheetName': site}}
            for i, site in enumerate(SITES[1:])]

        return {'requests': [sheets]}

    def _get_sheet_headers(self):
        """Get the request body to set the sheet headers."""
        values = [
            'Site',
            'Asset Tag',
            'Decom Date',
            'RT/Phab Purchase Task',
            'Unracked',
            'Wiped',
            'Hostname',
            'Service Tag',
            'Decom Task',
            'Warranty Expiry',
            'Model',
        ]

        return {
            'valueInputOption': 'USER_ENTERED',
            'data': [{'range': '{site}!A1:K1'.format(site=SITES[0]), 'values': [values]}],
        }

    def _get_format_request(self):
        """Get the request body to format the header row and columns."""
        requests = [
            # Make the first row center aligned and bold
            {'repeatCell': {
                'range': {'sheetId': 0, 'startRowIndex': 0, 'endRowIndex': 1},
                'cell': {
                    'userEnteredFormat': {
                        'horizontalAlignment' : 'CENTER',
                        'textFormat': {'bold': True},
                    }
                },
                'fields': 'userEnteredFormat(textFormat,horizontalAlignment)',
            }},
            # Make the first row fixed, rename to first site and set number of rows and columns
            {'updateSheetProperties': {
                'properties': {
                    'sheetId': 0,
                    'title': SITES[0],
                    'index': 0,
                    'gridProperties': {'frozenRowCount': 1, 'rowCount': 50, 'columnCount': 11}},
                'fields': '*',
            }},
            # Make the RT/Phab Task column wider enough to see the header
            {'updateDimensionProperties': {
                'range': {'sheetId': 0, 'dimension': 'COLUMNS', 'startIndex': 3, 'endIndex': 4},
                'properties': {'pixelSize': 160},
                'fields': 'pixelSize',
            }},
            # Make the device Model column wider enough to see the values
            {'updateDimensionProperties': {
                'range': {'sheetId': 0, 'dimension': 'COLUMNS', 'startIndex': 10, 'endIndex': 11},
                'properties': {'pixelSize': 180},
                'fields': 'pixelSize',
            }}
        ]

        return {'requests': requests}
