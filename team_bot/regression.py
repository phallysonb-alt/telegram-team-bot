"""Current regression gate; retired UI tests remain available for historical audits."""
import unittest
from .config import ROOT

RETIRED_CLASSES={
 'test_people_miniapp.PeopleTests':'人员 Mini App 审批窗口已按最终指令停用；自动群成员登记由 test_final_integration 验证',
 'test_workbench.WorkbenchUITests':'旧数量打卡界面已停用；旧模型与历史测试仍运行',
 'test_workbench_handler_acceptance.HandlerAcceptance':'旧打卡回调已停用；拒绝旧入口由 test_new_group 验证',
 'test_presentation.LayoutHandlerTests':'旧工作台及日报确认预览界面已停用；当前日报直接提交与 Mini App 分别测试',
}
RETIRED_METHODS={
 'test_progress_simple.SimpleTests.test_zero_unanswered_board_auto_creation_and_same_message_edit':'旧私聊面板同轮编辑已被累计锁定规则替代',
 'test_progress_simple.SimpleTests.test_notes_are_separate_full_multiline_revision_empty_and_owner_guard':'旧同轮修改问题规则已被提交后只读及各轮独立原文替代',
 'test_rich.RichTests.test_plain_commands_work_without_any_rich_display':'旧普通文字数量工作台入口已停用',
}

def suite():
    loaded=unittest.defaultTestLoader.discover(str(ROOT/'tests'));seen=set();cases=[]
    def flatten(items):
        for item in items:
            if isinstance(item,unittest.TestSuite):yield from flatten(item)
            else:yield item
    for case in flatten(loaded):
        key=case.id()
        if key in seen:continue
        seen.add(key);reason=RETIRED_METHODS.get(key) or RETIRED_CLASSES.get(key.rsplit('.',1)[0])
        if reason:
            # Explicit skip, never represented as a passed current test.
            setattr(case,case._testMethodName,unittest.skip(reason)(getattr(case,case._testMethodName)))
        cases.append(case)
    return unittest.TestSuite(cases)
