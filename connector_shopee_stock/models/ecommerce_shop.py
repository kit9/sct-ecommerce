# -*- coding: utf-8 -*-

from odoo import api, fields, models, exceptions
from datetime import datetime, timedelta
import logging
_logger= logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    need_tracking_no = fields.Boolean()

class eCommerceShop(models.Model):
    _inherit = 'ecommerce.shop'

    def _order_tracking_push_shopee(self, ordersn, tracking_no):
        self.env['sale.order'].search([('ecommerce_shop_id','=',self.id),('client_order_ref','=',ordersn)])[:1].picking_ids.write({
            'carrier_tracking_ref': tracking_no,
            })
        return True

    def _new_order_shopee(self, ordersn, detail=False):
        detail = detail or self._py_client_shopee().order.get_order_detail(ordersn_list=[ordersn])['orders'][0]
        order = super(eCommerceShop, self)._new_order_shopee(ordersn, detail=detail)
        order.carrier_id = self.env['ecommerce.carrier'].search([
            ('name','=', detail.get('shipping_carrier'))
            ])[:1].carrier_id
        for line in order.order_line:
            line.route_id = self.env.ref('connector_shopee_stock.stock_location_route_shopee')
        return order

    def _update_order_shopee(self, ordersn, status, update_time, detail=False):
        order = super(eCommerceShop, self)._update_order_shopee(ordersn, status, update_time, detail=detail)
        if status in ['READY_TO_SHIP','RETRY_SHIP']:
            order.need_tracking_no = True
            pick_ids = order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel'])
            pick_ids.write({'carrier_id': pick_ids.filtered('carrier_id')[:1].carrier_id.id})
        elif status == 'TO_CONFIRM_RECEIVE':
            shopee_pick_ids = order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel'] and r.picking_type_id == self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out'))
            for pick_id in shopee_pick_ids: 
                for line in pick_id.move_line_ids: 
                    if line.state not in ['done', 'cancel']: line.qty_done = line.product_uom_qty
                self.env['stock.immediate.transfer'].create({'pick_ids': [(4, pick_id.id)]}).process()

        elif status in ['TO_RETURN','CANCELLED']:
            pick_ids = order.picking_ids.filtered(lambda r: r.picking_type_id == self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out')) + order.picking_ids.filtered(lambda r: r.picking_type_id == order.warehouse_id.out_type_id)
            for pick_id in pick_ids:
                if pick_id.state not in ['done','cancel']: pick_id.action_cancel()
                elif pick_id.state == 'done':
                    returns = order.picking_ids.filtered(lambda r: r.location_id == pick_id.location_dest_id and r.location_dest_id == pick_id.location_id and r.state != 'cancel')
                    if not returns:
                        wiz_model = self.env['stock.return.picking']
                        wiz = wiz_model.create(wiz_model.with_context(active_id = pick_id.id).default_get(wiz_model._fields.keys()))
                        wiz.product_return_moves.write({'to_refund': True})
                        new_picking_id, pick_type_id = wiz._create_returns()
                        return_pick = self.env['stock.picking'].browse(new_picking_id)
                        return_pick.write({
                            'carrier_id': pick_id.carrier_id.id,
                            'carrier_tracking_ref': pick_id.carrier_tracking_ref
                        })
                        for moves in zip(pick_id.move_lines, return_pick.move_lines):
                            l = len(moves[1].move_line_ids)
                            moves[1].write({
                                'move_line_ids': [(1, moves[1].move_line_ids[i].id, {
                                    'lot_id': v.lot_id and v.lot_id.id
                                }) for i,v in enumerate(moves[0].move_line_ids[:l])] + [(0, _, {
                                    'product_uom_id': moves[1].product_uom.id,
                                    'picking_id': moves[1].picking_id.id,
                                    'move_id': moves[1].id,
                                    'product_id': moves[1].product_id.id,
                                    'location_id': moves[1].location_id.id,
                                    'location_dest_id': moves[1].location_dest_id.id,
                                    'lot_id': v.lot_id and v.lot_id.id
                                }) for i,v in enumerate(moves[0].move_line_ids[l:])]
                            })

        elif status == 'COMPLETED':
            pick_ids = order.picking_ids.filtered(lambda r: r.state not in ['done', 'cancel'] and r.picking_type_id in [self.env.ref('connector_shopee_stock.stock_picking_type_shopee_out'), self.env.ref('connector_shopee_stock.stock_picking_type_shopee_in')])
            for pick_id in pick_ids:
                #if pick_id.picking_type_id == self.env.ref('connector_shopee_stock.stock_picking_type_shopee_in'):
                #    source_pick = order.picking_ids.filtered(lambda r: r.location_id == pick_id.location_dest_id and r.location_dest_id == pick_id.location_id and r.state != 'cancel')[:1]
                #    if source_pick:
                #        for moves in zip(source_pick.move_lines, pick_id.move_lines):
                #            l = len(moves[1].move_line_ids)
                #            moves[1].write({
                #                'move_line_ids': [(1, moves[1].move_line_ids[i].id, {
                #                    'lot_id': v.lot_id and v.lot_id.id
                #                }) for i,v in enumerate(moves[0].move_line_ids[:l])] + [(0, _, {
                #                    'product_uom_id': moves[1].product_uom.id,
                #                    'picking_id': moves[1].picking_id.id,
                #                    'move_id': moves[1].id,
                #                    'product_id': moves[1].product_id.id,
                #                    'location_id': moves[1].location_id.id,
                #                    'location_dest_id': moves[1].location_dest_id.id,
                #                    'lot_id': v.lot_id and v.lot_id.id
                #                }) for i,v in enumerate(moves[0].move_line_ids[l:])]
                #            })
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
            ], limit=50)
        for order in orders:
            if all(order.picking_ids.filtered(lambda r: r.state not in ['done','cancel']).mapped('carrier_tracking_ref')):
                order.need_tracking_no = False
                continue
            logistic = False
            try:
                logistic = order.ecommerce_shop_id._py_client_shopee().logistic.get_order_logistic(ordersn=order.client_order_ref).get('logistics')
            except exceptions.UserError:
                pass
            if logistic:
                vals = {
                    'carrier_tracking_ref': logistic['tracking_no'],
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
            carrier = self.env['ecommerce.shop.carrier'].search([
                ('shop_id', '=', self.id),
                ('ecomm_carrier_id.logistic_idn','=',l.get('logistic_id'))
            ])
            if carrier: 
                carrier[0].write({
                    'enable': l.get('enabled'),
                    'default': l.get('preferred'),
                    'cod': l.get('has_cod'),
                })
            else:
                self.env['ecommerce.shop.carrier'].create({
                    'ecomm_carrier_id': self.env['ecommerce.carrier'].search([
                        ('platform_id','=', self.platform_id.id),
                        ('logistic_idn','=',l.get('logistic_id')),
                    ])[:1].id or self.env['ecommerce.carrier'].create({
                        'name': l.get('logistic_name'),
                        'logistic_idn': l.get('logistic_id'),
                        'platform_id': self.platform_id.id,
                        'carrier_id' : self.env['delivery.carrier'].create({
                            'name': l.get('logistic_name'),
                            'product_id': self.env.ref('delivery.product_product_delivery').id
                        }).id
                    }).id,
                    'shop_id': self.id,
                    'enable': l.get('enabled'),
                    'default': l.get('preferred'),
                    'cod': l.get('has_cod'),
                })
