/** @odoo-module **/

import { Activity } from "@mail/core/web/activity";
import { patch } from "@web/core/utils/patch";

patch(Activity.prototype, {
    async onClickStart() {
        await this.env.services.orm.call("mail.activity", "action_start", [this.props.activity.id]);
        this.props.onActivityChanged(this.thread);
    },
});
