# -*- coding: utf-8 -*-
###############################################################################
#    License, author and contributors information in:                         #
#    __manifest__.py file at the root folder of this module.                  #
###############################################################################

import datetime
import requests
import base64
import csv
import logging
import json
import io
import os
import datetime
# from requests_toolbelt.multipart.encoder import MultipartEncoder

from odoo import fields, models, _, api
from odoo.exceptions import UserError, AccessError
from odoo.http import request

_logger = logging.getLogger(__name__)

BATCH_API = "https://api.na.bambora.com/v1/batchpayments"
REPORT_API = "https://api.na.bambora.com/scripts/reporting/report.aspx"


# bamboraeft_merchant_id = '383610192'
# bamboraeft_merchant_id = '383610171'
# bambora_api_key = '6bA8eDe861234f208A457D4F915C2F27'
# bambora_api_key = '97F0CC62CDCf4a669Dc4345F69f23b92'
# bambora_report_api_key = 'f10ce943b0074D9bB0AAf06A8f4b2E77'

def bambora_payment(provider):
    ICPSudo = request.env['payment.acquirer'].sudo()
    bamboraeft_rec = ICPSudo.search([('provider', '=', provider)])
    if bamboraeft_rec:
        return bamboraeft_rec
    else:
        raise UserError(_("Module not install or disable!"))


# PASSCODE = 'Passcode ' + get_authorization(bamboraeft_merchant_id, bambora_api_key)


class AccountInvoiceBatchPayment(models.Model):
    _inherit = 'account.move'

    @api.model
    def _get_authorization(self, merchant_id, api_key):
        message = merchant_id + ":" + api_key
        base64_bytes = base64.b64encode(message.encode('ascii'))
        base64_message = base64_bytes.decode('ascii')
        _logger.info(base64_bytes.decode('ascii'))
        return base64_message

    batch_id = fields.Char('Batch ID', readonly=True)
    bambora_batch_payment_id = fields.Many2one('batch.payment.tracking', 'Bambora Batch Payment', readonly=True)
    bambora_batch_state = fields.Selection('Bambora Status', related='bambora_batch_payment_id.state')
    bambora_bank_identifier_number = fields.Char('Bank Identifier No.', related='invoice_partner_bank_id.bank_bic',
                                                 readonly=True)
    bambora_bank_transit_number = fields.Char('Bank Transit No.', related='invoice_partner_bank_id.bank_transit_no',
                                              readonly=True)

    # Bambora payment register
    def action_register_bambora_batch_payment(self):
        # ICPSudo = self.env['ir.config_parameter'].sudo()
        domain = [('provider', '=', 'bamboraeft')]
        domain += [('state', '!=', 'disabled')]
        acquirers = self.env['payment.acquirer'].sudo().search(domain)
        if not acquirers:
            raise UserError(_("Module not install or disable!"))

        PASSCODE = 'Passcode ' + self._get_authorization(acquirers.bamboraeft_merchant_id,
                                                         acquirers.bamboraeft_batch_api)
        data_list = []
        for record in self:
            if record.type == 'out_invoice':
                transaction_type = 'D'  # For invoice Debit
            elif record.type == 'in_invoice':
                transaction_type = 'C'  # For Vendor Bill Credit

            PayTrx = self.env['payment.transaction'].sudo()
            tx = PayTrx.search([('reference', '=', record.name)], limit=1)
            if tx:
                raise UserError(_("%s Record already in transaction process") % record.name)
            elif not record.invoice_partner_bank_id.acc_number or not record.bambora_bank_identifier_number or not record.bambora_bank_transit_number or not record.bambora_bank_identifier_number.isdigit() or not record.bambora_bank_transit_number.isdigit():
                    raise UserError(_("Please Add Full Account Information for  %s") % record.name)
            elif record.state == 'draft':
                raise UserError(_("Please only sent posted entries!! %s")%record.name)
            elif record.invoice_payment_state == 'paid':
                raise UserError(_("%s invoice Already Paid!!") % record.name)
            elif not len(record.bambora_bank_identifier_number) == 3 or not len(record.bambora_bank_transit_number) == 5:
                raise UserError(_("Bank identifier must be 3 digit and transit number is 5 digit!!. For %s") % record.name)
            else:
                data = ['E', transaction_type, record.bambora_bank_identifier_number, record.bambora_bank_transit_number, record.invoice_partner_bank_id.acc_number,
                        round(record.amount_total * 100), record.name, record.partner_id.name]
                data_list.append(data)

        folder_path = os.getenv('HOME') + '/bamboraFiles'
        if not os.path.isdir(folder_path):
            os.mkdir(folder_path)

        filename = os.path.expanduser(os.getenv('HOME')) + '/bamboraFiles/transaction.csv'
        with open(filename, 'w', encoding='UTF8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(data_list)

        dict_data = {
            "process_now": 1,
            # "process_date": datetime.date.today().strftime("%Y%m%d")
        }

        json_data = json.dumps(dict_data)

        files = (
            ('criteria', (None, json_data, 'application/json')),
            ('file', open(filename, 'rb'))
        )

        headers = {
            'authorization': PASSCODE,
            # 'filetype': 'STD',
            # 'content-type': 'multipart/form-data;boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW'
        }
        response = requests.post(BATCH_API, headers=headers, files=files)
        print("***********************************")
        print(response.status_code, response.text)

        #
        response_dict = json.loads(response.text)
        print(response_dict)
        #
        if response and response.status_code == 200:
            for rec in self:
                vals_list = {
                    "transaction_date": datetime.date.today(),
                    "invoice_no": rec.id,
                    "invoice_ref": rec.ref,
                    "invoice_partner_id": rec.partner_id.id,
                    "invoice_partner_bank_id": rec.invoice_partner_bank_id.id,
                    "invoice_date": rec.invoice_date,
                    "batch_id": response_dict['batch_id'],
                    "state": 'scheduled'
                }

                batch_id = self.env['batch.payment.tracking'].create(vals_list)
                rec.write({
                    'batch_id': response_dict['batch_id'],
                    'bambora_batch_payment_id': batch_id.id

                })

    @api.depends('commercial_partner_id')
    def _compute_bank_partner_id(self):
        for move in self:
            if move.is_outbound():
                move.bank_partner_id = move.commercial_partner_id
            else:
                move.bank_partner_id = move.commercial_partner_id
                # move.bank_partner_id = move.company_id.partner_id
