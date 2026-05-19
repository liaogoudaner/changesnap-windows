"""总结报告生成器。

使用 python-docx 生成格式化的 Word 变更总结报告。
遵循 PRD prd-data-model.md 第 4 节定义的报告结构和格式。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.session import SessionState, SessionStep
from utils.file_utils import ensure_dir, get_outputs_dir, safe_filename, get_file_size
from utils.log_utils import get_logger

logger = get_logger(__name__)

try:
    from docx import Document
    from docx.shared import Inches, Pt, Cm, RGBColor, Emu
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.section import WD_ORIENT
    from docx.oxml.ns import qn
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logger.warning("python-docx 未安装，报告生成功能不可用")


class ReportGenerator:
    """报告生成器。"""

    # 格式常量
    PAGE_WIDTH = Cm(21.0)
    PAGE_HEIGHT = Cm(29.7)
    MARGIN_TOP = Cm(2.54)
    MARGIN_BOTTOM = Cm(2.54)
    MARGIN_LEFT = Cm(3.17)
    MARGIN_RIGHT = Cm(3.17)

    FONT_TITLE = '黑体'
    FONT_BODY = '宋体'
    SIZE_TITLE = Pt(18)
    SIZE_CHAPTER = Pt(14)
    SIZE_BODY = Pt(10.5)
    SIZE_TABLE = Pt(9)
    SIZE_CAPTION = Pt(8)

    MAX_IMAGE_WIDTH = Cm(12)

    def __init__(self):
        self._doc = None

    def generate(
        self,
        session: SessionState,
        output_path: Optional[Path] = None,
    ) -> dict:
        """生成变更总结报告。

        Args:
            session: 会话状态
            output_path: 输出文件路径，默认自动生成

        Returns:
            包含生成结果的字典：
            - success: bool
            - filepath: str
            - file_size: int
            - elapsed: float
            - error: str (失败时)
        """
        if not DOCX_AVAILABLE:
            return {
                'success': False,
                'error': 'python-docx 未安装，无法生成报告',
            }

        start_time = time.time()

        try:
            self._doc = Document()

            # 页面设置
            self._setup_page()

            # 获取数据
            plan = session.plan_file.get('parsed', {}) or {}
            basic_info = plan.get('basic_info', {}) if isinstance(plan, dict) else {}
            time_personnel = plan.get('time_personnel', {}) if isinstance(plan, dict) else {}
            product_name = (basic_info.get('product_name', '未知产品')
                           if isinstance(basic_info, dict) else '未知产品')

            # 生成各章节
            self._add_title(product_name)
            self._add_overview(session, basic_info, time_personnel)
            self._add_change_sections(session)
            self._add_improvements(session)
            self._add_signatures(session)

            # 确定输出路径
            if output_path is None:
                now = datetime.now()
                date_str = now.strftime('%Y%m%d')
                name = safe_filename(product_name)
                filename = f"{name}变更总结报告_{date_str}.docx"
                output_dir = get_outputs_dir()
                output_path = output_dir / filename

            ensure_dir(output_path.parent)

            # 保存
            self._doc.save(str(output_path))
            elapsed = time.time() - start_time
            file_size = get_file_size(output_path)

            logger.info(f"报告生成成功: {output_path.name} ({file_size / 1024:.0f}KB, {elapsed:.1f}s)")

            return {
                'success': True,
                'filepath': str(output_path),
                'filename': output_path.name,
                'file_size': file_size,
                'elapsed': elapsed,
            }

        except Exception as e:
            logger.error(f"报告生成失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
            }

    def _setup_page(self):
        """页面设置。"""
        section = self._doc.sections[0]
        section.page_width = self.PAGE_WIDTH
        section.page_height = self.PAGE_HEIGHT
        section.top_margin = self.MARGIN_TOP
        section.bottom_margin = self.MARGIN_BOTTOM
        section.left_margin = self.MARGIN_LEFT
        section.right_margin = self.MARGIN_RIGHT

    def _add_title(self, product_name: str):
        """添加报告标题。"""
        title_text = f"{product_name}变更总结报告"

        # 空行
        self._doc.add_paragraph()

        p = self._doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(title_text)
        run.font.name = self.FONT_TITLE
        run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_TITLE)
        run.font.size = self.SIZE_TITLE
        run.bold = True

        # 分隔线
        p = self._doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run('─' * 40)
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(128, 128, 128)

    def _add_overview(self, session: SessionState, basic_info: dict, time_personnel: dict):
        """添加变更概况章节。"""
        self._add_chapter_title('一、变更概况')

        if isinstance(basic_info, dict):
            if basic_info.get('product_name'):
                self._add_info_line('产品名称', basic_info['product_name'])
            if basic_info.get('change_level'):
                self._add_info_line('变更等级', basic_info['change_level'])

        if isinstance(time_personnel, dict):
            if time_personnel.get('change_time'):
                self._add_info_line('变更时间', time_personnel['change_time'])

        # 实际耗时
        if session.start_time:
            try:
                start = datetime.fromisoformat(session.start_time)
                end = datetime.fromisoformat(session.end_time) if session.end_time else datetime.now()
                duration = (end - start).total_seconds() / 60
                self._add_info_line('实际耗时', f"{int(duration)}分钟")
            except (ValueError, TypeError):
                pass

        # 概况总结
        if session.summary and session.summary.overview:
            self._doc.add_paragraph()
            p = self._doc.add_paragraph()
            run = p.add_run('概况总结：')
            run.bold = True
            run.font.size = self.SIZE_BODY
            run.font.name = self.FONT_BODY
            run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

            p = self._doc.add_paragraph()
            run = p.add_run(session.summary.overview)
            run.font.size = self.SIZE_BODY
            run.font.name = self.FONT_BODY
            run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

    def _add_change_sections(self, session: SessionState):
        """添加变更具体环节各小节。

        包括：变更前检查 (pre_check)、变更实施 (implementation)、变更后验证 (test)
        """
        section_map = {
            'pre_check': ('二、变更具体环节', '2.1 变更前检查'),
            'implementation': ('二、变更具体环节', '2.2 变更实施'),
            'test': ('二、变更具体环节', '2.3 变更后验证'),
        }

        added_chapter = False
        for group in session.step_groups:
            if group.group_type in section_map:
                if not added_chapter:
                    self._add_chapter_title('二、变更具体环节')
                    added_chapter = True

                chapter, sub_title = section_map[group.group_type]
                self._add_sub_title(sub_title)

                # 生成步骤表格
                self._build_step_table(group.steps)

    def _add_improvements(self, session: SessionState):
        """添加总结和后续改进点。"""
        self._add_chapter_title('三、总结和后续改进点')

        if session.summary and session.summary.improvements:
            p = self._doc.add_paragraph()
            run = p.add_run(session.summary.improvements)
            run.font.size = self.SIZE_BODY
            run.font.name = self.FONT_BODY
            run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

    def _add_signatures(self, session: SessionState):
        """添加签名行。"""
        self._doc.add_paragraph()

        if session.summary:
            for label, field in [
                ('实施人', 'actual_implementer'),
                ('测试人', 'actual_tester'),
                ('审核人', 'actual_reviewer'),
            ]:
                value = getattr(session.summary, field, None)
                if value:
                    p = self._doc.add_paragraph()
                    run = p.add_run(f'{label}：{value}')
                    run.font.size = self.SIZE_BODY
                    run.font.name = self.FONT_BODY
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

    def _build_step_table(self, steps: list[SessionStep]):
        """构建步骤表格并嵌入截图。

        PRD prd-data-model.md 4.2 节：
        表格包含：序号、计划时间、操作步骤、实施人、审核人
        截图在表格行下方作为独立段落插入。
        """
        if not steps:
            return

        # 创建表格
        headers = ['序号', '计划时间', '操作步骤', '实施人', '审核人']
        table = self._doc.add_table(rows=1, cols=5)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = 'Table Grid'

        # 表头
        header_cells = table.rows[0].cells
        for i, h in enumerate(headers):
            header_cells[i].text = ''
            p = header_cells[i].paragraphs[0]
            run = p.add_run(h)
            run.bold = True
            run.font.size = self.SIZE_TABLE
            run.font.name = self.FONT_BODY
            run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 数据行
        for step in steps:
            if step.status == 'skipped':
                continue  # 跳过的不显示

            row_cells = table.add_row().cells

            # 序号
            row_cells[0].text = ''
            p = row_cells[0].paragraphs[0]
            run = p.add_run(str(step.seq))
            run.font.size = self.SIZE_TABLE
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # 计划时间（或实际时间）
            time_text = step.actual_time or step.planned_time
            row_cells[1].text = ''
            p = row_cells[1].paragraphs[0]
            run = p.add_run(time_text if time_text else '—')
            run.font.size = self.SIZE_TABLE
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # 操作步骤描述
            row_cells[2].text = ''
            p = row_cells[2].paragraphs[0]
            desc = step.description
            run = p.add_run(desc)
            run.font.size = self.SIZE_TABLE
            run.font.name = self.FONT_BODY
            run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

            # 实施人
            row_cells[3].text = ''
            p = row_cells[3].paragraphs[0]
            run = p.add_run(step.implementer if step.implementer else '—')
            run.font.size = self.SIZE_TABLE
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # 审核人
            row_cells[4].text = ''
            p = row_cells[4].paragraphs[0]
            run = p.add_run(step.reviewer if step.reviewer else '—')
            run.font.size = self.SIZE_TABLE
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # ---- 截图行（表格下方） ----
            kept_screenshots = [ss for ss in step.screenshots if ss.status in ('kept', 'active')]

            if kept_screenshots:
                # 截图并排显示（每行最多 2 张）
                for i in range(0, len(kept_screenshots), 2):
                    row_screenshots = kept_screenshots[i:i+2]
                    self._add_screenshot_row(row_screenshots)

                # 补充说明
                if step.supplement:
                    p = self._doc.add_paragraph()
                    run = p.add_run(f'补充说明：{step.supplement}')
                    run.italic = True
                    run.font.size = self.SIZE_CAPTION
                    run.font.name = self.FONT_BODY
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)
                    run.font.color.rgb = RGBColor(100, 100, 100)
            else:
                # 无截图提示
                p = self._doc.add_paragraph()
                run = p.add_run('（该步骤未截图）')
                run.font.size = self.SIZE_CAPTION
                run.font.color.rgb = RGBColor(180, 180, 180)

                if step.supplement:
                    p = self._doc.add_paragraph()
                    run = p.add_run(f'补充说明：{step.supplement}')
                    run.italic = True
                    run.font.size = self.SIZE_CAPTION
                    run.font.name = self.FONT_BODY
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

    def _add_screenshot_row(self, screenshots: list):
        """添加一行并排截图。"""
        # 使用一个两列无边框表格来并排显示截图
        num_screenshots = len(screenshots)
        if num_screenshots == 0:
            return

        # 每张截图宽度
        img_width = self.MAX_IMAGE_WIDTH
        if num_screenshots == 2:
            img_width = Cm(5.8)  # 两张并排

        for ss in screenshots:
            filepath = ss.filepath
            if not filepath or not os.path.exists(filepath):
                # 截图文件丢失
                p = self._doc.add_paragraph()
                run = p.add_run('[截图缺失]')
                run.font.size = self.SIZE_CAPTION
                run.font.color.rgb = RGBColor(255, 0, 0)
                continue

            try:
                # 添加截图
                p = self._doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run()
                run.add_picture(str(filepath), width=img_width)

                # 时间戳标注
                ts = ss.timestamp
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        ts_display = dt.strftime('%H:%M:%S')
                    except (ValueError, TypeError):
                        ts_display = ts
                else:
                    ts_display = ''

                if ts_display:
                    p = self._doc.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(f'截图时间：{ts_display}')
                    run.font.size = self.SIZE_CAPTION
                    run.font.color.rgb = RGBColor(128, 128, 128)
                    run.font.name = self.FONT_BODY
                    run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)

            except Exception as e:
                logger.warning(f"截图嵌入失败: {filepath}: {e}")
                p = self._doc.add_paragraph()
                run = p.add_run(f'[截图加载失败: {ss.filename}]')
                run.font.size = self.SIZE_CAPTION
                run.font.color.rgb = RGBColor(255, 0, 0)

    # ---- 辅助方法 ----

    def _add_chapter_title(self, text: str):
        """添加章节标题。"""
        self._doc.add_paragraph()  # 空行
        p = self._doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.name = self.FONT_TITLE
        run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_TITLE)
        run.font.size = self.SIZE_CHAPTER

    def _add_sub_title(self, text: str):
        """添加小节标题。"""
        p = self._doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.name = self.FONT_TITLE
        run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_TITLE)
        run.font.size = Pt(12)

    def _add_info_line(self, label: str, value: str):
        """添加信息行。"""
        p = self._doc.add_paragraph()
        run = p.add_run(f'{label}：{value}')
        run.font.size = self.SIZE_BODY
        run.font.name = self.FONT_BODY
        run._element.rPr.rFonts.set(qn('w:eastAsia'), self.FONT_BODY)
