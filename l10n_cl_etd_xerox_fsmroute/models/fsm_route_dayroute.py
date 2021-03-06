# Copyright (C) 2019 Open Source Integrators
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)
from odoo import fields, models, _
from odoo.addons.queue_job.job import job


class FSMDayRoute(models.Model):
    _name = 'fsm.route.dayroute'
    _inherit = ['fsm.route.dayroute', 'etd.mixin']

    def _compute_shipping(self):
        """
        Helper fields for Xerox file generation
        """
        ETDDocument = self.env['etd.document']
        force = self.env.context.get('xerox_force', False)
        for dayroute in self:
            # Include Invoices and Pickings linked to the DayRoute
            # regardless of their document date
            invoices_domain = ETDDocument._xerox_get_domain_invoice(
                force=force, dayroutes=self)
            dayroute.shipping_invoice_ids = \
                self.env['account.invoice'].search(invoices_domain)

            pickings_domain = ETDDocument._xerox_get_domain_picking(
                force=force, dayroutes=self)
            dayroute.shipping_picking_ids = \
                self.env['stock.picking'].search(pickings_domain)

            # Find Dayroute related Batch Pickings for loading operations
            # Loading operations are 'internal' picking type
            load_pick_domain = ETDDocument._xerox_get_domain_picking(
                force=force, dayroutes=self, picking_type="internal")
            load_pickings = self.env['stock.picking'].search(load_pick_domain)
            batchpicks_possible = load_pickings.mapped('batch_id')
            batchpick_domain = ETDDocument._xerox_get_domain_picking_batch(
                force=force, batchpicks=batchpicks_possible)
            dayroute.shipping_batchpick_ids = \
                self.env['stock.picking.batch'].search(batchpick_domain)

            # Total counts
            dayroute.xerox_pending_sign_count = (
                len(dayroute.shipping_invoice_ids) +
                len(dayroute.shipping_picking_ids) +
                len(dayroute.shipping_batchpick_ids)
            )
            truck_shipping_invoice_ids = (  # Only Boletas and Facturas
                dayroute.shipping_invoice_ids
                .filtered(lambda x: x.class_id.code in (33, 39))
            )
            dayroute.xerox_shipping_docs_count = (
                len(truck_shipping_invoice_ids) +
                len(dayroute.shipping_picking_ids) +
                len(dayroute.shipping_batchpick_ids)
            )

    shipping_invoice_ids = fields.Many2many(
        'account.invoice', compute='_compute_shipping')
    shipping_picking_ids = fields.Many2many(
        'stock.picking', compute='_compute_shipping')
    shipping_batchpick_ids = fields.Many2many(
        'stock.picking.batch', compute='_compute_shipping')
    xerox_pending_sign_count = fields.Integer(
        'Documents pending Xerox signing',
        compute='_compute_shipping')
    xerox_shipping_docs_count = fields.Integer(
        "Shipping documents to send to Xerox. "
        "Presales lots have shipping docs, "
        "Route closing docs don't.",
        compute="_compute_shipping")

    def _xerox_get_dayroute_rsets(self, force=False):
        """
        Returns a dict with the recordsets to send to Xerox
        """
        dayroutes = self.with_context(xerox_force=force)
        # Include Dayroutes with shipping documents to send
        # Only for presales lots, that have documents to ship
        dayroutes_with_docs = self.filtered('xerox_shipping_docs_count')
        # For presale
        invoices = dayroutes.mapped('shipping_invoice_ids')
        if self.env.context.get('xerox', False) == 61:
            # For liquidation
            invoices = invoices.filtered(lambda x: x.class_id.code == 61)
        return {
            'fsm.route.dayroute':
                dayroutes if force else dayroutes_with_docs,
            'account.invoice': invoices,
            'stock.picking': dayroutes.mapped('shipping_picking_ids'),
            'stock.batch.picking': dayroutes.mapped('shipping_batchpick_ids'),
        }

    @job
    def xerox_send_files(self, force=False):
        """
        Generate and send Xerox files for the selected Day Routes.
        One call per Company.
        """
        for company in self.mapped('company_id'):
            dayroutes = self.filtered(lambda x: x.company_id == company)
            rsets = dayroutes._xerox_get_dayroute_rsets(force=force)
            self.env['etd.document'].xerox_build_and_send_files(company, rsets)

    def action_xerox_send_files(self):
        """
        Generate and send Xerox files for the selected Day Routes
        One call per Company
        """
        # TODO: don't send if there is nothing to send
        # TODO: enable queue
        self.xerox_send_files()

    def action_xerox_send_files_force(self):
        self.xerox_send_files(force=True)

    # ==== Helpers for Xerox file templates ====

    def get_xerox_data(self):
        """
        Returns a list of lines for Xerox reports
        """
        lines = {}
        sections = {}
        moves = self.mapped(
            'shipping_batchpick_ids.picking_ids.move_ids_without_package')
        for line in moves:
            section_key = line.product_id.categ_id
            section_name = section_key.complete_name
            sections.setdefault(section_key, section_name or '')

            key = (line.product_id, line.product_uom)
            lines.setdefault(key, {
                'code': line.product_id.ref_etd,
                'name': line.product_id.name,
                'uom': line.product_uom.name,
                'quantity': 0,
                'price': 0,
                'section_key': section_key,
            })
            lines[key]['quantity'] += (
                line.quantity_done or line.product_uom_qty)
            lines[key]['price'] = max(
                lines[key]['price'] or 0,
                line.sale_line_id.price_unit or 0,
            )

        report_lines = []
        index = 1
        for section_key, section_name in sections.items():
            section_lines = [
                x for x in lines.values()
                if x['section_key'] == section_key]
            if section_lines:
                report_lines.append({
                    'index': index,
                    'code': '',
                    'name': _("***** %s *****") % section_name,
                    'uom': '',
                    'quantity': '',
                    'price': '',
                })
                index = index + 1
                for line in section_lines:
                    line['index'] = index
                    report_lines.append(line)
                    index = index + 1

        tot_qty = sum(x['quantity'] for x in lines.values())
        report_lines.append({
            'index': index,
            'code': '',
            'name': _('***** TOTAL *****'),
            'uom': _('TT'),
            'quantity': tot_qty,
            'price': '',
        })
        return report_lines
