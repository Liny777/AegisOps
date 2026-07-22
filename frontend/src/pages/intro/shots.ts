/**
 * 截图资产登记处（mock 模式下 Playwright 实拍，1280×800 @2x）。
 * 值为 null 时对应区块渲染占位图（不影响页面其余部分）。
 */
import workbench from "../../assets/intro/shot-workbench-chat.png";
import rca from "../../assets/intro/shot-rca-card.png";
import hitl from "../../assets/intro/shot-hitl-card.png";
import initWizard from "../../assets/intro/shot-init-wizard.png";

export const shots: {
  workbench: string | null;
  rca: string | null;
  hitl: string | null;
  initWizard: string | null;
} = {
  workbench,
  rca,
  hitl,
  initWizard,
};
