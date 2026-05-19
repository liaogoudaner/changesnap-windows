"""变更方案解析器。

支持 Word (.docx)、Excel (.xlsx/.xls)、JSON (.json)、YAML (.yml/.yaml) 格式。
解析后统一转换为 ChangePlan 数据模型。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional, Union

from models.change_plan import ChangePlan, BasicInfo, TimePersonnel, Sections, StepGroup, Step, Package
from utils.log_utils import get_logger

logger = get_logger(__name__)


# ----- Excel 解析关键常量 -----

HEADER_KEYWORDS = {
    'basic_info': ['产品名', '变更等级', '方案制定人员', '方案制定日期', '备注'],
    'time_personnel': ['变更时间', '产品接口人', '研发接口人', '运维接口人', '实施接口人'],
    'implementation_steps': ['计划时间', '操作步骤', '实施人', '审核人'],
    'rollback_steps': ['回退', '操作步骤', '实施人', '审核人'],
    'test_steps': ['测试', '验证', '操作步骤', '实施人', '审核人'],
    'on_duty': ['值守事项', '值守人', '值守方式'],
    'packages': ['版本号', '安装包名称', '类型'],
}


class PlanParseError(Exception):
    """方案解析异常。"""
    pass


class PlanParser:
    """方案解析器。"""

    @staticmethod
    def parse(file_path: Union[str, Path]) -> ChangePlan:
        """解析方案文件。

        Args:
            file_path: 方案文件路径

        Returns:
            解析后的 ChangePlan 对象

        Raises:
            PlanParseError: 解析失败时抛出
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise PlanParseError(f"文件不存在: {file_path}")

        ext = file_path.suffix.lower()
        filename = file_path.name

        if ext in ('.xlsx', '.xls', '.xlsm'):
            plan = PlanParser._parse_excel(file_path)
        elif ext == '.docx':
            plan = PlanParser._parse_docx(file_path)
        elif ext == '.json':
            plan = PlanParser._parse_json(file_path)
        elif ext in ('.yml', '.yaml'):
            plan = PlanParser._parse_yaml(file_path)
        else:
            raise PlanParseError(f"不支持的文件格式 '{ext}'，请使用 Word/Excel/JSON/YAML")

        plan.source_file = str(file_path)
        plan.source_format = ext.lstrip('.')

        logger.info(f"方案解析成功: {filename} (共 {plan.total_steps()} 个步骤)")
        return plan

    # ----- Word (docx) 解析 -----

    @staticmethod
    def _parse_docx(file_path: Path) -> ChangePlan:
        """解析 Word (.docx) 格式变更方案。

        基于标题样式（Heading 1/2/3）识别章节，提取表格和文本段落。
        """
        try:
            from docx import Document as DocxDocument
        except ImportError:
            raise PlanParseError("Word 解析需要 python-docx 库，请安装: pip install python-docx")

        try:
            doc = DocxDocument(str(file_path))
        except Exception as e:
            raise PlanParseError(f"Word 文件加载失败: {e}")

        plan = ChangePlan()
        current_section = None  # 当前所在的章节标题
        info_dict = {}          # 用于 key-value 表格的临时字典
        has_time_table = False

        # 按顺序遍历文档中的所有段落和表格
        from docx.text.paragraph import Paragraph as DocxParagraph
        from docx.table import Table as DocxTable

        for item in doc.iter_inner_content():
            if isinstance(item, DocxParagraph):
                # 段落
                text = item.text.strip()
                if not text:
                    continue

                style = item.style.name if item.style else ''
                if style.startswith('Heading') or style.startswith('标题'):
                    current_section = text
                    logger.debug(f"Word 章节: [{style}] {text}")
                elif current_section and text:
                    _collect_section_text(plan, current_section, text)

            elif isinstance(item, DocxTable):
                # 表格
                table = item
                if not table:
                    continue

                rows_data = []
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    rows_data.append(cells)

                if not rows_data:
                    continue

                header = rows_data[0]
                header_text = ' '.join(header)

                # 如果"表头"行看起来像数据行（第一个单元格是标签），则将其纳入数据
                data_rows = list(rows_data[1:])
                if _looks_like_data_row(header):
                    data_rows.insert(0, header)
                    header = [f'col{i}' for i in range(len(header))]  # 虚拟表头
                    header_text = ' '.join(header)

                # 基于当前章节标题和表头判断表格类型
                context = current_section or ''
                context_lower = context.lower()

                if _is_key_value_table(header, data_rows):
                    # key-value 表格（两列）
                    kv = {}
                    for row in data_rows:
                        if len(row) >= 2 and row[0]:
                            kv[row[0]] = row[1] if len(row) > 1 else ''
                        elif len(row) == 1 and row[0]:
                            kv[row[0]] = ''

                    all_keys = ' '.join(kv.keys())
                    if '变更时间' in all_keys or '接口人' in all_keys:
                        plan.time_personnel = TimePersonnel(
                            change_time=kv.get('变更时间', ''),
                            product_contact=kv.get('产品接口人', ''),
                            rd_contact=kv.get('研发接口人', ''),
                            ops_contact=kv.get('运维接口人', ''),
                            impl_contact=kv.get('实施接口人', ''),
                        )
                        has_time_table = True
                    elif '产品名' in all_keys or '变更等级' in all_keys or '制定人员' in all_keys:
                        plan.basic_info = BasicInfo(
                            product_name=kv.get('产品名', plan.basic_info.product_name),
                            author=kv.get('方案制定人员', ''),
                            plan_date=kv.get('方案制定日期', ''),
                            change_level=kv.get('变更等级', '一般'),
                            remarks=kv.get('备注', ''),
                        )

                elif _match_step_header(header_text):
                    # 步骤表格（有序号、操作步骤等列）
                    group_type = 'implementation'
                    group_name = '变更实施步骤'

                    if '回退' in context_lower or '回退' in header_text:
                        group_type = 'rollback'
                        group_name = '变更回退步骤'
                    elif '测试' in context_lower or '验证' in context_lower or '测试' in header_text:
                        group_type = 'test'
                        group_name = '变更测试步骤'
                    elif '检查' in context_lower or '准备' in context_lower or '检查' in header_text:
                        group_type = 'pre_check'
                        group_name = '变更前检查'
                    elif '值守' in context_lower:
                        group_type = 'on_duty'
                        group_name = '变更后值守'

                    # 如果有时间列但没有实施人列，可能是值守表
                    if group_type == 'implementation':
                        if '值守' in header_text or '值守人' in header_text:
                            group_type = 'on_duty'
                            group_name = '变更后值守'

                    # 从表头识别列索引
                    col_map = _map_step_columns(header)
                    seq_idx = col_map.get('seq', 0)
                    time_idx = col_map.get('time')
                    desc_idx = col_map.get('desc', -1)
                    impl_idx = col_map.get('impl')
                    review_idx = col_map.get('review')

                    steps = []
                    for row in data_rows:
                        seq_str = row[seq_idx].strip() if seq_idx < len(row) else ''
                        try:
                            seq = int(float(seq_str))
                        except (ValueError, TypeError):
                            if not seq_str:
                                continue
                            seq = len(steps) + 1

                        desc = row[desc_idx].strip() if 0 <= desc_idx < len(row) else ''
                        if not desc:
                            # 如果 desc 列没找到，用最长的非数字列
                            desc = max(row, key=len).strip() if row else ''

                        planned_time = row[time_idx].strip() if time_idx is not None and time_idx < len(row) and row[time_idx] else ''
                        impl = row[impl_idx].strip() if impl_idx is not None and impl_idx < len(row) and row[impl_idx] else ''
                        reviewer = row[review_idx].strip() if review_idx is not None and review_idx < len(row) and row[review_idx] else ''

                        if desc:
                            steps.append(Step(
                                seq=seq or len(steps) + 1,
                                description=desc,
                                planned_time=planned_time,
                                implementer=impl,
                                reviewer=reviewer,
                            ))

                    if steps:
                        plan.step_groups.append(StepGroup(
                            group_type=group_type,
                            group_name=group_name,
                            steps=steps,
                        ))

        # 确保有时间人员信息（即使没找到专门表格）
        if not has_time_table and plan.time_personnel.change_time:
            pass  # 已从 sections 或文本中提取

        if not plan.step_groups:
            raise PlanParseError("Word 方案中未找到变更步骤表格，请检查文件格式")

        return plan

    # ----- JSON 解析 -----

    @staticmethod
    def _parse_json(file_path: Path) -> ChangePlan:
        """解析 JSON 格式方案。"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise PlanParseError(f"JSON 解析失败: {e}")
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    data = json.load(f)
            except Exception as e:
                raise PlanParseError(f"JSON 解析失败（尝试 GBK 编码后）: {e}")

        return PlanParser._dict_to_plan(data)

    # ----- YAML 解析 -----

    @staticmethod
    def _parse_yaml(file_path: Path) -> ChangePlan:
        """解析 YAML 格式方案。"""
        try:
            import yaml
        except ImportError:
            raise PlanParseError("YAML 解析需要 PyYAML 库，请安装: pip install pyyaml")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PlanParseError(f"YAML 解析失败: {e}")
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    data = yaml.safe_load(f)
            except Exception as e:
                raise PlanParseError(f"YAML 解析失败（尝试 GBK 编码后）: {e}")

        if not isinstance(data, dict):
            raise PlanParseError("YAML 文件内容格式错误，期望一个对象")

        return PlanParser._dict_to_plan(data)

    # ----- Excel 解析 -----

    @staticmethod
    def _parse_excel(file_path: Path) -> ChangePlan:
        """解析 Excel 格式方案。"""
        try:
            import openpyxl
        except ImportError:
            raise PlanParseError("Excel 解析需要 openpyxl 库，请安装: pip install openpyxl")

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        except Exception as e:
            raise PlanParseError(f"Excel 文件加载失败: {e}")

        plan = ChangePlan()
        found_any_table = False

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]

            try:
                rows = list(ws.iter_rows(values_only=False))
            except Exception:
                continue

            if not rows:
                continue

            # 尝试识别表格区域
            tables = PlanParser._extract_tables(rows)

            for table in tables:
                header_row = table['header']
                data_rows = table['data']
                header_text = [str(c.value or '').strip() for c in header_row]

                found_any_table = True

                # 判断表格类型并解析
                if PlanParser._match_header(header_text, HEADER_KEYWORDS['basic_info']):
                    PlanParser._parse_basic_info_table(plan, header_row, data_rows)

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['time_personnel']):
                    PlanParser._parse_time_personnel_table(plan, header_row, data_rows)

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['implementation_steps']):
                    if any(kw in ' '.join(header_text) for kw in ['回退']):
                        PlanParser._parse_steps_table(plan, header_row, data_rows, 'rollback', '变更回退步骤')
                    elif any(kw in ' '.join(header_text) for kw in ['值守']):
                        PlanParser._parse_on_duty_table(plan, header_row, data_rows)
                    else:
                        PlanParser._parse_steps_table(plan, header_row, data_rows, 'implementation', '变更实施步骤')

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['rollback_steps']):
                    PlanParser._parse_steps_table(plan, header_row, data_rows, 'rollback', '变更回退步骤')

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['test_steps']):
                    PlanParser._parse_steps_table(plan, header_row, data_rows, 'test', '变更测试步骤')

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['on_duty']):
                    PlanParser._parse_on_duty_table(plan, header_row, data_rows)

                elif PlanParser._match_header(header_text, HEADER_KEYWORDS['packages']):
                    PlanParser._parse_packages_table(plan, header_row, data_rows)

        wb.close()

        if not found_any_table:
            raise PlanParseError("无法解析方案文件，请检查模板格式。文件中未找到可识别的表格。")

        if not plan.step_groups:
            # 如果没有找到任何步骤组但有实施步骤表, 默认创建一个
            raise PlanParseError("方案中未找到变更步骤，请检查文件")

        return plan

    @staticmethod
    def _extract_tables(rows: list) -> list[dict]:
        """从行数据中提取表格区域（表头+数据行）。"""
        tables = []
        current_table = None

        for row in rows:
            values = [c.value for c in row]
            non_empty = sum(1 for v in values if v is not None and str(v).strip() != '')
            if non_empty == 0:
                # 空行，结束当前表格
                if current_table and current_table['data']:
                    tables.append(current_table)
                current_table = None
                continue

            if current_table is None:
                current_table = {'header': row, 'data': []}
            else:
                current_table['data'].append(row)

        if current_table and current_table['data']:
            tables.append(current_table)

        return tables

    @staticmethod
    def _match_header(header_text: list[str], keywords: list[str]) -> bool:
        """检查表头是否匹配关键字。"""
        joined = ' '.join(header_text)
        matched = sum(1 for kw in keywords if kw in joined)
        return matched >= 2  # 至少匹配 2 个关键字

    @staticmethod
    def _get_cell_value(cell) -> str:
        """获取单元格的字符串值。"""
        if cell is None or cell.value is None:
            return ''
        return str(cell.value).strip()

    @staticmethod
    def _parse_basic_info_table(plan: ChangePlan, header_row, data_rows):
        """解析变更基本信息表（key-value 格式）。"""
        info = {}
        for row in data_rows:
            cells = [PlanParser._get_cell_value(c) for c in row]
            if len(cells) >= 2 and cells[0]:
                info[cells[0]] = cells[1] if len(cells) > 1 else ''

        plan.basic_info = BasicInfo(
            product_name=info.get('产品名', ''),
            author=info.get('方案制定人员', ''),
            plan_date=info.get('方案制定日期', ''),
            change_level=info.get('变更等级', '一般'),
            remarks=info.get('备注', ''),
        )

    @staticmethod
    def _parse_time_personnel_table(plan: ChangePlan, header_row, data_rows):
        """解析时间人员表（key-value 格式）。"""
        info = {}
        for row in data_rows:
            cells = [PlanParser._get_cell_value(c) for c in row]
            if len(cells) >= 2 and cells[0]:
                info[cells[0]] = cells[1] if len(cells) > 1 else ''

        plan.time_personnel = TimePersonnel(
            change_time=info.get('变更时间', ''),
            product_contact=info.get('产品接口人', ''),
            rd_contact=info.get('研发接口人', ''),
            ops_contact=info.get('运维接口人', ''),
            impl_contact=info.get('实施接口人', ''),
        )

    @staticmethod
    def _parse_steps_table(plan: ChangePlan, header_row, data_rows,
                           group_type: str, group_name: str):
        """解析步骤表格。"""
        headers = [PlanParser._get_cell_value(c) for c in header_row]

        # 确定列索引
        seq_idx = None
        time_idx = None
        desc_idx = None
        impl_idx = None
        review_idx = None

        for i, h in enumerate(headers):
            if '序号' in h:
                seq_idx = i
            elif '计划时间' in h or '时间' in h:
                time_idx = i
            elif '操作步骤' in h or '步骤' in h or '操作' in h or '值守事项' in h:
                desc_idx = i
            elif '实施人' in h:
                impl_idx = i
            elif '审核人' in h:
                review_idx = i

        if desc_idx is None:
            desc_idx = 1  # 默认第 2 列

        steps = []
        for row in data_rows:
            cells = [PlanParser._get_cell_value(c) for c in row]
            desc = cells[desc_idx] if desc_idx < len(cells) else ''
            if not desc:
                continue  # 跳过空行

            seq_str = cells[seq_idx] if seq_idx is not None and seq_idx < len(cells) else ''
            seq = 0
            try:
                seq = int(float(seq_str))
            except (ValueError, TypeError):
                seq = len(steps) + 1

            steps.append(Step(
                seq=seq,
                description=desc,
                planned_time=cells[time_idx] if time_idx is not None and time_idx < len(cells) else '',
                implementer=cells[impl_idx] if impl_idx is not None and impl_idx < len(cells) else '',
                reviewer=cells[review_idx] if review_idx is not None and review_idx < len(cells) else '',
            ))

        # 如果组是 implementation 类型，检查是否存在 pre_check 步骤（带检查关键字）
        if group_type == 'implementation':
            pre_check_steps = []
            impl_steps = []
            for s in steps:
                if any(kw in s.description for kw in ['检查', '查看', '检测', '确认', '备份']):
                    pre_check_steps.append(s)
                else:
                    impl_steps.append(s)

            if pre_check_steps and impl_steps:
                # 拆分两组
                plan.step_groups.append(StepGroup(
                    group_type='pre_check',
                    group_name='变更前检查',
                    steps=pre_check_steps,
                ))
                plan.step_groups.append(StepGroup(
                    group_type='implementation',
                    group_name='变更实施',
                    steps=impl_steps,
                ))
                return

        # 正常添加
        plan.step_groups.append(StepGroup(
            group_type=group_type,
            group_name=group_name,
            steps=steps,
        ))

    @staticmethod
    def _parse_on_duty_table(plan: ChangePlan, header_row, data_rows):
        """解析值守安排表。"""
        steps = []
        for i, row in enumerate(data_rows):
            cells = [PlanParser._get_cell_value(c) for c in row]
            if not cells[0] and not (len(cells) > 1 and cells[1]):
                continue
            desc = ' | '.join(c for c in cells if c)
            steps.append(Step(
                seq=i + 1,
                description=desc,
                implementer=cells[1] if len(cells) > 1 else '',
                reviewer=cells[2] if len(cells) > 2 else '',
            ))

        plan.step_groups.append(StepGroup(
            group_type='on_duty',
            group_name='值守安排',
            steps=steps,
        ))

    @staticmethod
    def _parse_packages_table(plan: ChangePlan, header_row, data_rows):
        """解析代码包清单表。"""
        headers = [PlanParser._get_cell_value(c) for c in header_row]

        version_idx = None
        type_idx = None
        name_idx = None

        for i, h in enumerate(headers):
            if '版本' in h:
                version_idx = i
            elif '类型' in h or '类别' in h:
                type_idx = i
            elif '安装包' in h or '名称' in h or '包名' in h:
                name_idx = i

        for row in data_rows:
            cells = [PlanParser._get_cell_value(c) for c in row]
            name = cells[name_idx] if name_idx is not None and name_idx < len(cells) else ''
            if not name:
                continue

            plan.packages.append(Package(
                version=cells[version_idx] if version_idx is not None and version_idx < len(cells) else '',
                pkg_type=cells[type_idx] if type_idx is not None and type_idx < len(cells) else '',
                name=name,
            ))

    # ----- 字典到 Plan 的转换 -----

    @staticmethod
    def _dict_to_plan(data: dict) -> ChangePlan:
        """将字典数据转换为 ChangePlan。"""
        # 处理 JSON Schema 格式（从 step_groups 读取）
        plan = ChangePlan()

        if 'basic_info' in data:
            plan.basic_info = BasicInfo.from_dict(data['basic_info'])

        if 'time_personnel' in data:
            plan.time_personnel = TimePersonnel.from_dict(data['time_personnel'])

        if 'sections' in data:
            sections_data = data['sections']
            if isinstance(sections_data, dict):
                plan.sections = Sections.from_dict(sections_data)

        if 'step_groups' in data:
            for g in data['step_groups']:
                if isinstance(g, dict):
                    plan.step_groups.append(StepGroup.from_dict(g))

        if 'packages' in data:
            for p in data['packages']:
                if isinstance(p, dict):
                    plan.packages.append(Package.from_dict(p))

        # 如果没有 step_groups，尝试从顶层 steps 字段读取
        if not plan.step_groups and 'steps' in data:
            steps = []
            for i, s in enumerate(data['steps']):
                if isinstance(s, dict):
                    steps.append(Step.from_dict(s))
            if steps:
                plan.step_groups.append(StepGroup(
                    group_type='implementation',
                    group_name='变更实施步骤',
                    steps=steps,
                ))

        # 验证关键字段
        if not plan.basic_info.product_name:
            plan.basic_info.product_name = data.get('product_name', '未命名方案')
        if not plan.time_personnel.change_time:
            plan.time_personnel.change_time = data.get('change_time', '')

        return plan

    # ----- 验证 -----

    @staticmethod
    def validate_plan(plan: ChangePlan) -> list[str]:
        """验证方案数据的完整性，返回警告列表。"""
        warnings = []

        if not plan.basic_info.product_name:
            warnings.append("产品名为空")

        if not plan.time_personnel.change_time:
            warnings.append("变更时间为空")

        if not plan.step_groups:
            warnings.append("未包含任何步骤分组")

        for group in plan.step_groups:
            if not group.steps:
                warnings.append(f"步骤组 '{group.group_name}' ({group.group_type}) 为空")
            for step in group.steps:
                if not step.description:
                    warnings.append(f"步骤 {step.seq} 描述为空")

        return warnings


# ----- Word 解析辅助函数 -----


def _paragraph_from_xml(doc, xml_el):
    """将 w:p XML 元素转为 python-docx Paragraph 对象。"""
    from docx.text.paragraph import Paragraph
    return Paragraph(xml_el, doc)


def _table_from_xml(doc, xml_el):
    """将 w:tbl XML 元素转为 python-docx Table 对象。"""
    from docx.table import Table
    return Table(xml_el, doc)


def _is_key_value_table(header: list[str], data_rows: list = None) -> bool:
    """判断表格是否为 key-value 格式（2列，1列是标签，2列是值）。"""
    labels = ('产品名', '变更时间', '接口人', '制定人员', '变更等级', '备注')
    # 检查表头
    if len(header) == 2:
        if any(lbl in header[0] for lbl in labels):
            return True
    # 检查数据行第一列
    if data_rows:
        for row in data_rows[:3]:  # 只看前3行
            if row and any(lbl in str(row[0]) for lbl in labels):
                return True
    return False


def _match_step_header(header_text: str) -> bool:
    """判断表头是否为步骤表格（有序号、操作步骤等列）。"""
    keywords = ['序号', '操作步骤', '实施人', '审核人', '值守事项', '值守人']
    return sum(1 for kw in keywords if kw in header_text) >= 2


def _is_time_range(text: str) -> bool:
    """判断文本是否为时间范围格式（如 23:30-23:35）。"""
    import re
    return bool(re.match(r'^\d{1,2}:\d{2}[–\-~]\d{1,2}:\d{2}$', text.strip()))


def _looks_like_data_row(row: list[str]) -> bool:
    """判断一行是否看起来像数据行而非表头。"""
    if not row:
        return False
    first = row[0].strip()
    data_labels = ['产品名', '变更时间', '变更等级', '方案制定', '产品接口',
                   '研发接口', '运维接口', '实施接口', '备注', '编号']
    return any(lbl in first for lbl in data_labels)


def _map_step_columns(header: list[str]) -> dict:
    """根据表头识别各列的索引。

    Returns:
        {'seq': int, 'time': int|None, 'desc': int, 'impl': int|None, 'review': int|None}
    """
    col_map = {'seq': 0, 'time': None, 'desc': -1, 'impl': None, 'review': None}
    for i, h in enumerate(header):
        h_clean = h.strip()
        if '序号' in h_clean:
            col_map['seq'] = i
        elif '计划时间' in h_clean or ('时间' in h_clean and '计划' in h_clean):
            col_map['time'] = i
        elif '操作步骤' in h_clean or '步骤' in h_clean or '值守事项' in h_clean:
            col_map['desc'] = i
        elif '实施人' in h_clean:
            col_map['impl'] = i
        elif '审核人' in h_clean:
            col_map['review'] = i
        elif '值守人' in h_clean:
            col_map['impl'] = i

    # 如果没有 desc 列，用最长文本的列
    if col_map['desc'] == -1 and len(header) >= 3:
        col_map['desc'] = 2

    # 值守表特殊处理：3列 = 值守事项 | 值守人 | 值守方式
    if any('值守' in h for h in header):
        if col_map['desc'] == -1:
            col_map['desc'] = 0
        if col_map['impl'] is None and len(header) > 1:
            col_map['impl'] = 1
        if col_map['review'] is None and len(header) > 2:
            col_map['review'] = 2

    return col_map


def _collect_section_text(plan: ChangePlan, section: str, text: str):
    """将段落文本收集到对应的 section 字段。"""
    section_lower = section.lower()
    s = plan.sections

    if '变更目的' in section:
        s.purpose = (s.purpose + '\n' + text).strip() if s.purpose else text
    elif '用户解释' in section or '口径' in section:
        s.user_communication = (s.user_communication + '\n' + text).strip() if s.user_communication else text
    elif '现网' in section or '当前现状' in section or '总体情况' in section:
        s.current_status_overview = (s.current_status_overview + '\n' + text).strip() if s.current_status_overview else text
    elif '网络拓扑' in section:
        s.current_status_network = (s.current_status_network + '\n' + text).strip() if s.current_status_network else text
    elif '系统架构' in section:
        s.current_status_architecture = (s.current_status_architecture + '\n' + text).strip() if s.current_status_architecture else text
    elif '准备' in section:
        s.preparation = (s.preparation + '\n' + text).strip() if s.preparation else text
    elif '目的' in section_lower:
        s.purpose = (s.purpose + '\n' + text).strip() if s.purpose else text
