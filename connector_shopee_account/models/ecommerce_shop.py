#-*- coding:utf-8 -*-

from odoo import fields, models, api, _
from odoo.tools.float_utils import float_round, float_compare, float_is_zero
from datetime import datetime, timedelta
import pytz, json
import logging
_logger = logging.getLogger(__name__)

class eCommerceShop(models.Model):
    _inherit = 'ecommerce.shop'


    def _update_order_shopee(self, ordersn, status, update_time, detail=False):
        if status in ['SHIPPED', 'COMPLETED']:
            detail = detail or self._py_client_shopee().order.get_order_detail(ordersn_list=[ordersn])['orders'][0]
            order = super(eCommerceShop, self)._update_order_shopee(ordersn, status, update_time, detail=detail)
            precision_digits = self.env['decimal.precision'].precision_get('Product Price')
            if float_compare(float(detail.get('escrow_amount')), order.amount_untaxed, precision_digits=precision_digits) != 0:
                order.write({
                    'order_line': [(0, _, {
                        'product_id': self.env.ref('connector_ecommerce_common_account.product_product_ecommerce_expense').id,
                        'price_unit': float(detail.get('escrow_amount')) - order.amount_untaxed
                    })]
                })
            invoice_ids = order.action_invoice_create(final=True)
            for invoice in self.env['account.invoice'].browse(invoice_ids):
                if invoice.state == "draft": invoice.action_invoice_open()
            return order
        else:
            return super(eCommerceShop, self)._update_order_shopee(ordersn, status, update_time, detail=detail)

    @api.multi
    def _sync_transaction_shopee(self, **kw):
        self.ensure_one()
        kw.setdefault('pagination_offset', 0)
        kw.setdefault('pagination_entries_per_page', 100)
        # avoiding duplicate by adding 1 to last_sync timestamp
        kw.setdefault('create_time_from', self._last_transaction_sync and int(self._last_transaction_sync.timestamp()+1) \
            or int((datetime.now() - timedelta(days=7)).timestamp()))
        kw.setdefault('create_time_to', int(datetime.now().timestamp()))
        transaction_list = []
        while True:
            _logger.info(kw)
            resp = self._py_client_shopee().execute("wallet/transaction/list", "POST", kw)
            transaction_list += resp['transaction_list']
            if not resp['has_more']: break
            kw['pagination_offset'] += kw['pagination_entries_per_page']

        if transaction_list:
            transaction_list = transaction_list[::-1]
            stmt = self.env['account.bank.statement'].create({
                'journal_id': self.journal_id.id,
                'name': fields.Datetime.now().astimezone(pytz.timezone(self.env.user.tz or 'UTC')).strftime('%Y-%m-%d'),
            })
            stmt.onchange_journal_id()
            stmt.write({
                'line_ids': [(0, _, {
                    'date': datetime.fromtimestamp(t['create_time']).astimezone(pytz.timezone(self.env.user.tz or 'UTC')).strftime('%Y-%m-%d'),
                    'name': t['ordersn'] and '{}: {}'.format(t['transaction_type'], t['ordersn']) or t['transaction_type'],
                    'partner_id': self.env['res.partner'].search([('ref','=',t['buyer_name'])])[:1].id,
                    'ref': t['transaction_id'],
                    'amount': t['amount'],
                    'note': json.dumps({k: v for k,v in t.items() if v}),
                }) for t in transaction_list],
            })
            stmt.balance_end_real = stmt.balance_end
            account_rcv = self.env['account.account'].search([('user_type_id.type', '=', 'receivable')], limit=1)
            for line in stmt.line_ids:
                if line.name.startswith('ESCROW_VERIFIED_ADD'):
                    counterpart_aml_dicts = []
                    _logger.info(line.name)
                    for ml in self.env['account.move.line'].search([
                        ('account_id','=',line.partner_id and line.partner_id.property_account_receivable_id.id or account_rcv.id),
                        ('name','=', line.name.split('ESCROW_VERIFIED_ADD: ')[-1]),
                        ('move_id.state','=','posted')
                    ]):
                        amount = ml.currency_id and ml.amount_residual_currency or ml.amount_residual
                        counterpart_aml_dicts.append({
                            'move_line': ml,
                            'name': ml.name,
                            'debit': amount < 0 and -amount or 0,
                            'credit': amount > 0 and amount or 0,
                        })
                    _logger.info(counterpart_aml_dicts)
                    line.process_reconciliation(counterpart_aml_dicts=counterpart_aml_dicts)
            self._last_transaction_sync = datetime.fromtimestamp(kw.get('create_time_to'))

