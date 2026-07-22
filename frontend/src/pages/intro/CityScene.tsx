import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { color } from "../../theme/tokens";

/**
 * Hero 背景：浅色 Three.js 城市场景（移植自用户 Openclaw-Docker 仓库的 OpsDiveScene，
 * 去掉 9 步分镜叙事，改为缓慢自动运镜 + 鼠标轻视差的纯背景组件）。
 * - 浅色化：透明底 + pageBg 同色雾，楼体浅灰白，顶盖/光环走品牌蓝，告警楼栋保留红色呼吸信标
 * - 性能：antialias off / pixelRatio≤1.5 / 集显友好 powerPreference；页面隐藏时暂停 RAF
 * - 兜底：无 WebGL 返回 null（hero 渐变底自然兜底）；prefers-reduced-motion 只渲染一帧
 */

const CITY_CENTER = new THREE.Vector3(4, 0, 8);

/** 调用链节点（沿用参考场景布点，作为城市中的「主链路」亮线） */
const TRACE_NODES: { position: THREE.Vector3; height: number; accent: string }[] = [
  { position: new THREE.Vector3(-86, 0, -42), height: 18, accent: "#14b8a6" },
  { position: new THREE.Vector3(-48, 0, -16), height: 24, accent: "#14b8a6" },
  { position: new THREE.Vector3(-14, 0, 8), height: 28, accent: "#38bdf8" },
  { position: new THREE.Vector3(24, 0, 28), height: 20, accent: "#14b8a6" },
  { position: new THREE.Vector3(62, 0, 48), height: 18, accent: "#14b8a6" },
];

/** 告警楼栋（红色呼吸信标——原场景最有辨识度的细节） */
const ALARM_BUILDINGS = [
  { position: new THREE.Vector3(-60, 0, 46), height: 26 },
  { position: new THREE.Vector3(-4, 0, 34), height: 20 },
  { position: new THREE.Vector3(48, 0, 26), height: 23 },
  { position: new THREE.Vector3(-28, 0, 6), height: 18 },
];

/** 滚动分帧机位：一帧对应页面一个区块（Hero → 页脚 CTA），滚动切帧后镜头阻尼飞行过渡。 */
const CAMERA_FRAMES: { position: THREE.Vector3; lookAt: THREE.Vector3 }[] = [
  // 0 Hero：高空全景
  { position: new THREE.Vector3(-48, 120, 165), lookAt: new THREE.Vector3(4, 6, 8) },
  // 1 三大核心能力：下降拉近城市上空
  { position: new THREE.Vector3(-30, 78, 118), lookAt: new THREE.Vector3(0, 8, 12) },
  // 2 工作方式：低机位看主链路亮线与节点楼
  { position: new THREE.Vector3(-60, 38, 96), lookAt: new THREE.Vector3(-14, 10, 8) },
  // 3 能力矩阵：侧向环绕节点楼群（redis 方向）
  { position: new THREE.Vector3(105, 55, 105), lookAt: new THREE.Vector3(24, 10, 28) },
  // 4 使用教程：移向告警街区
  { position: new THREE.Vector3(-100, 42, 100), lookAt: new THREE.Vector3(-60, 10, 46) },
  // 5 安全与可控：红色告警楼特写（信标呼吸成为焦点）
  { position: new THREE.Vector3(-32, 26, 78), lookAt: new THREE.Vector3(-60, 16, 46) },
  // 6 页脚 CTA：拉回高空俯瞰收尾
  { position: new THREE.Vector3(60, 150, 190), lookAt: new THREE.Vector3(4, 0, 8) },
];

function frameAt(step: number): { position: THREE.Vector3; lookAt: THREE.Vector3 } {
  return CAMERA_FRAMES[Math.min(Math.max(step, 0), CAMERA_FRAMES.length - 1)];
}

function supportsWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** 确定性伪随机（同种子布局稳定，避免每次进页城市都变样） */
function seededRandom(seed: number): () => number {
  let value = seed;
  return () => {
    value |= 0;
    value = (value + 0x6d2b79f5) | 0;
    let t = Math.imul(value ^ (value >>> 15), 1 | value);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 楼栋：楼体 + 半透明顶盖 + 地面光环（浅色变体） */
function makeBuilding(
  position: THREE.Vector3,
  width: number,
  depth: number,
  height: number,
  bodyColor: string,
  accent: string,
  capOpacity: number,
): THREE.Group {
  const group = new THREE.Group();
  group.position.copy(position);

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(width, height, depth),
    new THREE.MeshStandardMaterial({ color: bodyColor, roughness: 0.85, metalness: 0.04 }),
  );
  body.position.y = height / 2;
  group.add(body);

  const cap = new THREE.Mesh(
    new THREE.BoxGeometry(width * 1.05, 0.55, depth * 1.05),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: capOpacity, depthWrite: false }),
  );
  cap.position.y = height + 0.35;
  group.add(cap);

  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(Math.max(width, depth) * 0.72, 0.08, 8, 48),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.22, depthWrite: false }),
  );
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.16;
  group.add(ring);

  return group;
}

function lineBetween(points: THREE.Vector3[], lineColor: string, opacity: number): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({ color: lineColor, transparent: true, opacity });
  return new THREE.Line(geometry, material);
}

export function CityScene({ step = 0 }: { step?: number }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const webglOk = useMemo(() => supportsWebGL(), []);
  // step 走 ref：切帧只改镜头目标，不重建场景（与参考实现 stepRef 同款）
  const stepRef = useRef(step);
  useEffect(() => {
    stepRef.current = step;
  }, [step]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !webglOk) return;

    let renderer: THREE.WebGLRenderer | null = null;
    let rafId = 0;
    let resizeObserver: ResizeObserver | null = null;
    let running = document.visibilityState === "visible";
    const disposables: { dispose(): void }[] = [];

    const track = <T extends { dispose(): void }>(d: T): T => {
      disposables.push(d);
      return d;
    };

    try {
      const scene = new THREE.Scene();
      // 透明底 + 与页面底色同色的雾：远景楼群融进 hero 渐变，不显生硬边界。
      // 雾区间必须显著大于相机-城心距离（约 205），否则浅雾浅楼直接融成空白。
      scene.fog = new THREE.Fog(color.pageBg, 150, 460);

      const camera = new THREE.PerspectiveCamera(44, 1, 0.1, 900);

      renderer = new THREE.WebGLRenderer({
        antialias: false,
        alpha: true,
        powerPreference: "default",
        preserveDrawingBuffer: false,
      });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.domElement.style.position = "absolute";
      renderer.domElement.style.inset = "0";
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      renderer.domElement.style.pointerEvents = "none";
      mount.appendChild(renderer.domElement);

      // 浅色光照：环境光压低一点、平行光加强，靠侧面明暗差让白楼在浅底上立体可读
      scene.add(new THREE.AmbientLight("#ffffff", 0.85));
      const sun = new THREE.DirectionalLight("#dbe7f7", 1.9);
      sun.position.set(-80, 140, 70);
      scene.add(sun);

      const cityGroup = new THREE.Group();
      scene.add(cityGroup);

      const ground = new THREE.Mesh(
        track(new THREE.PlaneGeometry(300, 270)),
        track(new THREE.MeshStandardMaterial({ color: color.surfaceAlt, roughness: 0.95, metalness: 0 })),
      );
      ground.rotation.x = -Math.PI / 2;
      ground.position.y = -0.05;
      cityGroup.add(ground);

      const grid = new THREE.GridHelper(280, 28, color.brandTintBorder, color.borderInner);
      (grid.material as THREE.Material).transparent = true;
      (grid.material as THREE.Material).opacity = 0.5;
      cityGroup.add(grid);

      // 背景楼群：种子随机布局（与参考场景同参数），浅灰白楼体 + 少量品牌蓝顶盖
      const random = seededRandom(73);
      for (let ix = -4; ix <= 4; ix++) {
        for (let iz = -3; iz <= 3; iz++) {
          if ((ix + iz) % 3 === 0) continue;
          const x = ix * 20 + (random() - 0.5) * 5;
          const z = iz * 19 + (random() - 0.5) * 5;
          const pos = new THREE.Vector3(x, 0, z);
          if (TRACE_NODES.some((n) => n.position.distanceTo(pos) < 13)) continue;
          const height = 5 + random() * 18;
          const highlight = random() > 0.72;
          const building = makeBuilding(
            pos,
            5 + random() * 4,
            5 + random() * 5,
            height,
            highlight ? "#ffffff" : "#f1f4f9",
            highlight ? color.brand : "#b9c6d8",
            highlight ? 0.3 : 0.14,
          );
          cityGroup.add(building);
        }
      }

      // 主链路节点楼 + 蓝色调用链亮线
      TRACE_NODES.forEach((n, i) => {
        const building = makeBuilding(
          n.position,
          i === 3 ? 12 : 10,
          i === 3 ? 12 : 9,
          n.height,
          "#ffffff",
          n.accent,
          0.5,
        );
        cityGroup.add(building);
      });
      const tracePoints = TRACE_NODES.map((n) => n.position.clone().setY(n.height + 3));
      cityGroup.add(lineBetween(tracePoints, color.brand, 0.5));

      // 告警楼栋：浅色楼体 + 红色呼吸信标/光晕
      const beacons: { mesh: THREE.Mesh; halo: THREE.Mesh; material: THREE.MeshBasicMaterial; haloMaterial: THREE.MeshBasicMaterial; phase: number }[] = [];
      ALARM_BUILDINGS.forEach((alarm, i) => {
        const building = makeBuilding(alarm.position, 10, 10, alarm.height, "#fff6f5", color.danger, 0.3);
        const beaconMaterial = new THREE.MeshBasicMaterial({
          color: color.danger,
          transparent: true,
          opacity: 0.8,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        });
        const beaconMesh = new THREE.Mesh(track(new THREE.SphereGeometry(2.1, 18, 18)), track(beaconMaterial));
        beaconMesh.position.set(0, alarm.height + 4.5, 0);
        building.add(beaconMesh);
        const haloMaterial = new THREE.MeshBasicMaterial({
          color: "#f08a84",
          transparent: true,
          opacity: 0.4,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        });
        const halo = new THREE.Mesh(track(new THREE.TorusGeometry(5.2, 0.16, 8, 64)), track(haloMaterial));
        halo.rotation.x = Math.PI / 2;
        halo.position.set(0, alarm.height + 1.2, 0);
        building.add(halo);
        cityGroup.add(building);
        beacons.push({ mesh: beaconMesh, halo, material: beaconMaterial, haloMaterial, phase: i * 1.7 });
      });

      // 相机：阻尼追踪当前滚动帧的机位（参考实现的 lerp 0.035/0.045 手感），
      // 帧目标上叠加轻微升降 + 鼠标视差，帧内也保持微动不死板。
      const pointer = { x: 0, y: 0 };
      const pointerSmooth = { x: 0, y: 0 };
      const onPointerMove = (event: PointerEvent) => {
        pointer.x = (event.clientX / window.innerWidth - 0.5) * 2;
        pointer.y = (event.clientY / window.innerHeight - 0.5) * 2;
      };
      window.addEventListener("pointermove", onPointerMove);

      const initialFrame = frameAt(stepRef.current);
      camera.position.copy(initialFrame.position);
      const lookAtSmooth = initialFrame.lookAt.clone();
      const desired = new THREE.Vector3();

      const placeCamera = (t: number) => {
        pointerSmooth.x += (pointer.x - pointerSmooth.x) * 0.04;
        pointerSmooth.y += (pointer.y - pointerSmooth.y) * 0.04;
        const frame = frameAt(stepRef.current);
        desired
          .copy(frame.position)
          .add(new THREE.Vector3(pointerSmooth.x * 6, Math.sin(t * 0.0002) * 2.5 - pointerSmooth.y * 5, 0));
        camera.position.lerp(desired, 0.035);
        lookAtSmooth.lerp(frame.lookAt, 0.045);
        camera.lookAt(lookAtSmooth);
      };

      const resize = () => {
        if (!renderer) return;
        const w = mount.clientWidth || 1;
        const h = mount.clientHeight || 1;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      resize();
      resizeObserver = new ResizeObserver(() => {
        resize();
        // 尺寸变化后补一帧：隐藏标签页（RAF 暂停）也靠这帧保持画面正确
        if (renderer) {
          placeCamera(0);
          renderer.render(scene, camera);
        }
      });
      resizeObserver.observe(mount);

      const renderFrame = (t: number) => {
        placeCamera(t);
        beacons.forEach((b) => {
          const pulse = (Math.sin(t * 0.0032 + b.phase) + 1) / 2;
          b.material.opacity = 0.35 + pulse * 0.5;
          b.haloMaterial.opacity = 0.16 + pulse * 0.3;
          const s = 1 + pulse * 0.35;
          b.mesh.scale.setScalar(s);
          b.halo.scale.setScalar(1 + pulse * 0.2);
        });
        renderer?.render(scene, camera);
      };

      // 先出一帧：后台/隐藏标签页（RAF 被节流或 running=false）也至少有静态画面
      placeCamera(0);
      renderer.render(scene, camera);

      // 动画始终运行（用户明确要求背景常动；场景缓慢低刺激，故不做 reduced-motion 静态降级），
      // 仅在页面切后台时暂停渲染省电。
      const loop = (t: number) => {
        rafId = requestAnimationFrame(loop);
        if (!running) return;
        renderFrame(t);
      };
      rafId = requestAnimationFrame(loop);

      const onVisibility = () => {
        running = document.visibilityState === "visible";
      };
      document.addEventListener("visibilitychange", onVisibility);

      return () => {
        cancelAnimationFrame(rafId);
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("pointermove", onPointerMove);
        resizeObserver?.disconnect();
        scene.traverse((obj) => {
          if (obj instanceof THREE.Mesh || obj instanceof THREE.Line) {
            obj.geometry?.dispose();
            const m = obj.material as THREE.Material | THREE.Material[] | undefined;
            if (Array.isArray(m)) m.forEach((mm) => mm.dispose());
            else m?.dispose();
          }
        });
        disposables.forEach((d) => d.dispose());
        renderer?.dispose();
        if (renderer && renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
        renderer = null;
      };
    } catch {
      // WebGL 初始化失败（驱动黑名单等）：静默降级为纯渐变背景
      if (renderer) {
        renderer.dispose();
        if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
      }
      return;
    }
  }, [webglOk]);

  if (!webglOk) return null;
  return <div ref={mountRef} aria-hidden style={{ position: "absolute", inset: 0, overflow: "hidden" }} />;
}
