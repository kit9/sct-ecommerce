# -*- coding: utf-8 -*-

from odoo import api, fields, models
from datetime import datetime, timedelta
import logging
_logger= logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    need_tracking_no = fields.Boolean()

class eCommerceShop(models.Model):
    _inherit = 'ecommerce.shop'

#    def order_tracking_no_push(self, ordersn, tracking_no):
#        self.env['sale.order'].search([('ecommerce_shop_id','=',self.id),('client_order_ref','=',ordersn)])[:1].picking_ids.write({
#            'carrier_tracking_ref': tracking_no,
#            })
#        return True

    def _new_order_shopee(self, ordersn, status, update_time):
        resp = self._py_client_shopee().order.get_order_detail(ordersn_list=[ordersn])
        order = super(eCommerceShop, self)._new_order_shopee(ordersn, status, update_time, resp=resp)
        order.carrier_id = self.env['ecommerce.carrier'].search([
            ('name','=', resp.get('orders') and resp['orders'][0].get('shipping_carrier'))
            ])[:1].carrier_id
        for line in order.order_line:
            line.route_id = self.env.ref('connector_shopee_stock.stock_location_route_shopee')
        return order

    def _update_order_shopee(self, ordersn, status, update_time):
        order = super(eCommerceShop, self)._update_order_shopee(ordersn, status, update_time)
        if status in ['READY_TO_SHIP','RETRY_SHIP']:
            order.need_tracking_no = True

        elif status == 'TO_CONFIRM_RECEIVE':
            shopee_pick_ids = order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel'] and r.picking_type_id == self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out'))
            for pick_id in shopee_pick_ids: 
                for line in pick_id.move_line_ids: 
                    if line.state not in ['done', 'cancel']: line.qty_done = line.product_uom_qty
                self.env['stock.immediate.transfer'].create({'pick_ids': [(4, pick_id.id)]}).process()

        elif status == 'TO_RETURN': 
            pick_ids = order.picking_ids.filtered(lambda r: r.picking_type_id in [self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out'), order.warehouse_id.out_type_id])
            for pick_id in pick_ids: 
                if pick_id.state not in ['done','cancel']: pick_id.action_cancel()
                elif pick_id.state == 'done': 
                    wiz = self.env['stock.picking.return'].create({'picking_id': pick_id.id})
                    wiz.product_return_moves.write({'to_refund': True})
                    wiz.create_returns()

        elif status == 'COMPLETED':
            pick_ids = order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel'] and r.picking_type_id in [self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out'), self.env.ref('connector_shopee_stock.stock_picking_type_shopee_in')])
            for pick_id in pick_ids:
                for line in pick_id.move_line_ids: 
                    if line.state not in ['done', 'cancel']: line.qty_done = line.product_uom_qty
                self.env['stock.immediate.transfer'].create({'pick_ids': [(4, pick_id.id)]}).process()
        return order

    @api.model
    def get_tracking_no(self):
        orders = self.env['sale.order'].search([
            ('ecommerce_shop_id.platform_id.platform','=','shopee'),
            ('state','=', 'sale'),
            ('need_tracking_no','=',True),
            ('confirmation_date','>',(datetime.now()-timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S"))
            ], limit=200)
        for order in orders:
            logistic = order.ecommerce_shop_id._py_client_shopee().logistic.get_order_logistic(ordersn=order.client_order_ref).get('logistics')
            if logistic:
                vals = {
                    'carrier_tracking_ref': logistic['tracking_no'],
                    'carrier_id': order.carrier_id.id,
                    }
                if not order.carrier_id: 
                    order.carrier_id = self.env['ecommerce.carrier'].search([('logistic_idn','=', logistic['logistic_id'])])[:1].carrier_id
                    vals.update({'carrier_id': order.carrier_id.id})
                order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel']).write(vals)
                order.need_tracking_no = False

    def _get_logistic_shopee(self):
        self.ensure_one()
        logistics = self._py_client_shopee().logistic.get_logistics().get('logistics',[])
        for l in logistics:
            self.env['ecommerce.carrier'].create({
                'name': l.get('logistic_name'),
                'logistic_idn': l.get('logistic_id'),
                'enable': l.get('enabled'),
                'default': l.get('preferred'),
                'cod': l.get('has_cod'),
                'platform_id': self.platform_id.id,
                'carrier_id' : self.env['delivery.carrier'].create({
                    'name': l.get('logistic_name'),
                    'product_id': self.env.ref('delivery.product_product_delivery').id
                    }).id
                })
