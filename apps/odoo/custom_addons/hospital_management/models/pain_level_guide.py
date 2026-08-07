from odoo import fields, models


class HospitalPainLevelGuide(models.TransientModel):
    _name = "hospital.pain.level.guide"
    _description = "Pain Level Guide"

    guide_html = fields.Html(
        string="Pain Level Guide",
        default=lambda self: self._default_guide_html(),
        readonly=True,
    )

    def _default_guide_html(self):
        return """
            <div style="display:flex; gap:48px; align-items:flex-start; padding:16px;">
                <div style="min-width:320px;">
                    <div style="display:flex; align-items:stretch; gap:18px;">
                        <div style="width:28px; height:360px; background:linear-gradient(#c40018, #ff8a00, #27a844, #00a7c8); border-radius:2px;"></div>
                        <div style="display:grid; grid-template-rows:repeat(11, 1fr); height:360px; font-size:18px; font-weight:700;">
                            <div>10</div><div>9</div><div>8</div><div>7</div><div>6</div><div>5</div>
                            <div>4</div><div>3</div><div>2</div><div>1</div><div>0</div>
                        </div>
                        <div style="display:grid; grid-template-rows:repeat(6, 1fr); height:360px; font-size:22px; font-weight:700; text-align:center;">
                            <div style="width:58px; height:58px; border-radius:50%; background:#c40018; color:#fff; line-height:58px;">&gt;_&lt;</div>
                            <div style="width:58px; height:58px; border-radius:50%; background:#e02a2a; color:#fff; line-height:58px;">:'(</div>
                            <div style="width:58px; height:58px; border-radius:50%; background:#ff8a00; color:#fff; line-height:58px;">:(</div>
                            <div style="width:58px; height:58px; border-radius:50%; background:#ffa51f; color:#fff; line-height:58px;">:/</div>
                            <div style="width:58px; height:58px; border-radius:50%; background:#27a844; color:#fff; line-height:58px;">:)</div>
                            <div style="width:58px; height:58px; border-radius:50%; background:#00a7c8; color:#fff; line-height:58px;">:D</div>
                        </div>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-top:8px; font-weight:700;">
                        <span>No Pain</span>
                        <span>Worst Possible Pain</span>
                    </div>
                </div>
                <div style="max-width:420px; font-size:15px; line-height:1.55;">
                    <p><strong>10</strong> - Worst possible pain.</p>
                    <p><strong>9</strong> - Excruciating pain.</p>
                    <p><strong>7-8</strong> - Intense pain.</p>
                    <p><strong>5-6</strong> - Moderate to severe pain.</p>
                    <p><strong>3-4</strong> - Moderate pain.</p>
                    <p><strong>1-2</strong> - Mild pain.</p>
                    <p><strong>0</strong> - No pain.</p>
                </div>
            </div>
        """
