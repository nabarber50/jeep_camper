# cam/setup/foamcam/cam_ops.py
import adsk.core, adsk.cam, adsk.fusion
import math
from foamcam.models import CamBuildResult
from foamcam.geometry import model_xy_extents_mm


class CamBuilder:
    def __init__(self, cam: adsk.cam.CAM, design, units, logger, Config, enforcer=None):
        self.cam = cam
        self.design = design
        self.units = units
        self.logger = logger
        self.Config = Config
        self.enforcer = enforcer

        # Robust startup diagnostics for Config values (helps detect runtime mutations)
        swap = getattr(self.Config, 'MASLOW_SWAP_XY_COMPENSATION', '<missing>')
        r_val = getattr(self.Config, 'MASLOW_ROTATE_SHEET_BODIES', '<missing>')
        try:
            rotate_sheets = bool(r_val)
        except Exception:
            rotate_sheets = '<error>'
        try:
            self.logger.log(
                f'CamBuilder init: MASLOW_SWAP_XY_COMPENSATION={swap} '
                f'MASLOW_ROTATE_SHEET_BODIES_raw={r_val} '
                f'MASLOW_ROTATE_SHEET_BODIES_bool={rotate_sheets} '
                f'Config_id={id(self.Config)} type={type(self.Config)} dir_contains={"MASLOW_ROTATE_SHEET_BODIES" in dir(self.Config)}'
            )
        except Exception as e:
            try:
                self.logger.log(f'CamBuilder init diagnostic failed: {e}')
            except:
                pass

    def _apply_xy_swap_compensation_rotation(self, sheet_component) -> bool:
        """Guarded and concise implementation for sheet-body rotation.

        Decision is logged as a single line per sheet indicating rotated=True/False
        and the reason. Rotation only occurs if both MASLOW_SWAP_XY_COMPENSATION
        and MASLOW_ROTATE_SHEET_BODIES are True and the model's long axis is X.
        """
        try:
            try:
                name = sheet_component.name
            except:
                name = '(unnamed sheet)'

            # Quick global check: require explicit True on MASLOW_SWAP_XY_COMPENSATION
            if getattr(self.Config, 'MASLOW_SWAP_XY_COMPENSATION', False) is not True:
                self.logger.log(
                    f'ROTATION DECISION: sheet={name} rotated=False reason=MASLOW_SWAP_XY_COMPENSATION_NOT_TRUE '
                    f'MASLOW_SWAP_XY_COMPENSATION={getattr(self.Config, "MASLOW_SWAP_XY_COMPENSATION", None)}'
                )
                return False

            # Already compensated?
            try:
                attrs = getattr(sheet_component, 'attributes', None)
                if attrs:
                    existing = attrs.itemByName('foamcam', 'xy_swap_compensated')
                    if existing:
                        self.logger.log(f'ROTATION DECISION: sheet={name} rotated=False reason=ALREADY_COMPENSATED')
                        return False
            except:
                pass

            # Gather model bodies
            bodies = adsk.core.ObjectCollection.create()
            model_bodies = []
            for b in sheet_component.bRepBodies:
                try:
                    if b and b.isSolid:
                        bodies.add(b)
                        model_bodies.append(b)
                except:
                    pass

            if bodies.count == 0:
                self.logger.log(f'ROTATION DECISION: sheet={name} rotated=False reason=NO_SOLID_BODIES')
                return False

            # Check config opt-in for rotating sheet bodies: require explicit True
            rotate_sheets = getattr(self.Config, 'MASLOW_ROTATE_SHEET_BODIES', False) is True
            if not rotate_sheets:
                self.logger.log(
                    f'ROTATION DECISION: sheet={name} rotated=False reason=ROTATE_DISABLED_BY_CONFIG '
                    f'MASLOW_ROTATE_SHEET_BODIES={getattr(self.Config, "MASLOW_ROTATE_SHEET_BODIES", None)}'
                )
                return False

            # Extents check — avoid rotating if long axis already Y
            ex = model_xy_extents_mm(model_bodies)
            if ex:
                mx, my = ex
                if mx < my:
                    self.logger.log(f'ROTATION DECISION: sheet={name} rotated=False reason=LONG_AXIS_ALREADY_Y modelX={mx:.1f} modelY={my:.1f}')
                    return False

            # Compute pivot (center of bounding box)
            bbox = None
            for i in range(bodies.count):
                try:
                    b = bodies.item(i)
                    bb = b.boundingBox
                    if not bb:
                        continue
                    if bbox is None:
                        bbox = bb
                    else:
                        mn = bbox.minPoint
                        mx = bbox.maxPoint
                        bbmn = bb.minPoint
                        bbmx = bb.maxPoint
                        mn = adsk.core.Point3D.create(min(mn.x, bbmn.x), min(mn.y, bbmn.y), min(mn.z, bbmn.z))
                        mx = adsk.core.Point3D.create(max(mx.x, bbmx.x), max(mx.y, bbmx.y), max(mx.z, bbmx.z))
                        bbox = adsk.core.BoundingBox3D.create(mn, mx)
                except:
                    pass

            pivot = adsk.core.Point3D.create(0, 0, 0)
            try:
                if bbox:
                    mn = bbox.minPoint
                    mx = bbox.maxPoint
                    pivot = adsk.core.Point3D.create((mn.x + mx.x) / 2.0, (mn.y + mx.y) / 2.0, (mn.z + mx.z) / 2.0)
            except:
                pass

            # Prepare rotation
            mf = sheet_component.features.moveFeatures
            m = adsk.core.Matrix3D.create()
            m.setToRotation(math.radians(90.0), adsk.core.Vector3D.create(0, 0, 1), pivot)

            # Dev-only fail-fast guard (disabled by default)
            if bool(getattr(self.Config, 'DEBUG_FAIL_ON_ROTATION', False)):
                raise RuntimeError(f'DEBUG_FAIL_ON_ROTATION triggered for sheet: {name}')

            try:
                inp = mf.createInput(bodies, m)
                mf.add(inp)
            except Exception as e:
                self.logger.log(f'ROTATION DECISION: sheet={name} rotated=False reason=ROTATION_ADD_FAILED error={e}')
                return False

            # Mark as compensated
            try:
                if attrs:
                    attrs.add('foamcam', 'xy_swap_compensated', '1')
            except:
                pass

            self.logger.log(f'ROTATION DECISION: sheet={name} rotated=True reason=APPLIED')
            return True

        except Exception as e:
            self.logger.log(f'ROTATION DECISION: sheet={name} rotated=False reason=EXCEPTION error={e}')
            return False

    def _find_tool_best_effort(self):
        name_key = (self.Config.PREFERRED_TOOL_NAME_CONTAINS or '').lower()

        def scan_tools(tools):
            try:
                for i in range(tools.count):
                    t = tools.item(i)
                    try:
                        if name_key and name_key in (t.name or '').lower():
                            return t
                    except:
                        pass
            except:
                pass
            return None

        try:
            app = adsk.core.Application.get()
            cam_mgr = getattr(app, "camManager", None)
            if cam_mgr:
                lm = getattr(cam_mgr, "libraryManager", None)
                if lm:
                    libs = getattr(lm, "toolLibraries", None)
                    if libs:
                        for li in range(libs.count):
                            lib = libs.item(li)
                            tools = getattr(lib, "tools", None)
                            if tools:
                                t = scan_tools(tools)
                                if t:
                                    return t
        except:
            pass
        return None

    def _set_expr(self, params, name, expr):
        try:
            p = params.itemByName(name)
            if p:
                p.expression = expr
                return True
        except:
            pass
        return False

    def _set_bool(self, params, name, val: bool):
        try:
            p = params.itemByName(name)
            if p:
                try:
                    p.value = val
                except:
                    p.expression = 'true' if val else 'false'
                return True
        except:
            pass
        return False

    def _apply_maslow_z(self, op: adsk.cam.Operation):
        p = op.parameters

        def set_expr(name, expr):
            self._set_expr(p, name, expr)

        set_expr('retractHeight_offset',   self.Config.MASLOW_RETRACT)
        set_expr('clearanceHeight_offset', self.Config.MASLOW_CLEARANCE)
        set_expr('feedHeight_offset',      self.Config.MASLOW_FEED)

        set_expr('plungeFeedrate',   self.Config.MASLOW_PLUNGE_FEED)
        set_expr('retractFeedrate',  self.Config.MASLOW_RETRACT_FEED)

        set_expr('tool_feedPlunge',  self.Config.MASLOW_PLUNGE_FEED)
        set_expr('tool_feedRetract', self.Config.MASLOW_RETRACT_FEED)

        try:
            ar = p.itemByName('allowRapidRetract')
            if ar:
                try:
                    ar.value = False
                except:
                    ar.expression = 'false'
        except:
            pass

    def _create_2d_contour_input_best_effort(self, ops, ui, warn_state: dict):
        candidates = [
            '2dContour','2DContour','contour2d','contour2D','Contour2D','2d-contour','2d_contour','2dContourOp',
            '2dProfile','2DProfile','profile2d','profile2D','2d-profile','2d_profile',
            'mill2dContour','Milling2DContour','trace',
        ]
        last_err = None
        for s in candidates:
            try:
                return ops.createInput(s)
            except Exception as e:
                last_err = e

        if not warn_state.get("warned", False):
            warn_state["warned"] = True
            ui.messageBox(
                "This Fusion build does not expose a 2D Contour strategy ID via API.\n\n"
                "Workaround:\n"
                "- Script will create Adaptive + Scallop.\n"
                "- Add 2D Contour manually once, then save as a Template.\n\n"
                f"Last error: {last_err}"
            )
        return None

    def create_for_sheets(self, sheets, ui) -> CamBuildResult:
        warn_state = {"warned": False}
        tool = None

        try:
            tool = self._find_tool_best_effort()
            if tool:
                self.logger.log(f"Tool auto-picked: {tool.name}")
            else:
                self.logger.log("Tool auto-pick unavailable; ops will be created without tool selection.")
        except:
            tool = None

        def try_assign_tool(op):
            if not tool:
                return
            try:
                op.tool = tool
            except:
                pass

        result = CamBuildResult()

        for sheet in sheets:
            occ = sheet.occ

            setup_in = self.cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
            setup = self.cam.setups.add(setup_in)
            try:
                setup.name = f'CAM_{occ.component.name}'
            except:
                setup.name = 'CAM_Sheet'

            # Caller-side guard: do not call rotation method unless both
            # MASLOW_SWAP_XY_COMPENSATION and MASLOW_ROTATE_SHEET_BODIES are
            # explicitly True. This prevents older in-memory implementations
            # from performing an undesired rotation when the caller opts out.
            caller_swap = getattr(self.Config, 'MASLOW_SWAP_XY_COMPENSATION', None)
            caller_rotate = getattr(self.Config, 'MASLOW_ROTATE_SHEET_BODIES', None)
            if caller_swap is True and caller_rotate is True:
                self.logger.log(f'CALLSITE: invoking rotation: MASLOW_SWAP_XY_COMPENSATION={caller_swap} MASLOW_ROTATE_SHEET_BODIES={caller_rotate}')
                self._apply_xy_swap_compensation_rotation(occ.component)
            else:
                self.logger.log(f'CALLSITE: skipping rotation: MASLOW_SWAP_XY_COMPENSATION={caller_swap} MASLOW_ROTATE_SHEET_BODIES={caller_rotate}')

            # models = all solid bodies in sheet component
            coll = adsk.core.ObjectCollection.create()
            model_bodies = []
            try:
                for b in occ.component.bRepBodies:
                    if b and b.isSolid:
                        coll.add(b)
                        model_bodies.append(b)
            except:
                pass

            try:
                setup.models = coll
            except:
                pass

            try:
                setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
            except:
                pass

            # enforce orientation + stock + origin (best effort)
            enforced_ok = False
            if self.enforcer:
                try:
                    self.enforcer.enforce(setup, model_bodies)
                    enforced_ok = True
                except Exception as e:
                    result.enforcement_failures += 1
                    self.logger.log(f"{setup.name}: orientation enforcement FAILED: {e}")

            ops = setup.operations

            # 2D contour best effort
            prof_in = None
            try:
                prof_in = self._create_2d_contour_input_best_effort(ops, ui, warn_state)
            except:
                prof_in = None

            if prof_in:
                prof_in.displayName = 'Foam Cutout 2D (Profile)'
                prof = ops.add(prof_in)
                try_assign_tool(prof)

                self._set_bool(prof.parameters, 'doRoughingPasses', True)
                self._set_bool(prof.parameters, 'doMultipleDepths', True)
                self._set_expr(prof.parameters, 'maximumStepdown', self.Config.PROFILE_STEPDOWN)

                self._apply_maslow_z(prof)

            # 3D adaptive
            try:
                rough_in = ops.createInput('adaptive')
                rough_in.displayName = 'Foam Rough 3D (Adaptive)'
                rough = ops.add(rough_in)
                try_assign_tool(rough)

                self._set_expr(rough.parameters, 'maximumStepdown', self.Config.ROUGH_STEPDOWN)
                self._apply_maslow_z(rough)
            except Exception as e:
                ui.messageBox(f"Failed creating Adaptive op in {setup.name}:\n{e}")

            # 3D scallop
            try:
                fin_in = ops.createInput('scallop')
                fin_in.displayName = 'Foam Finish 3D (Scallop)'
                fin = ops.add(fin_in)
                try_assign_tool(fin)

                self._set_expr(fin.parameters, 'finishingStepdown', self.Config.FINISH_STEPDOWN)
                self._set_expr(fin.parameters, 'maximumStepdown', self.Config.FINISH_STEPDOWN)
                self._apply_maslow_z(fin)
            except Exception as e:
                ui.messageBox(f"Failed creating Scallop op in {setup.name}:\n{e}")

            result.setups_created += 1
            try:
                adsk.doEvents()
            except:
                pass

        ui.messageBox(
            "CAM creation complete.\n\n"
            f"Setups created: {result.setups_created}\n"
            f"Orientation enforcement failures: {result.enforcement_failures}\n\n"
            "Notes:\n"
            "- If 2D Contour cannot be created by API, add it manually once and save a Template.\n"
            "- If Maslow is still 90° off, your build likely needs a different WCS rotation param —\n"
            "  run once and paste the WCS-related param dump from the log."
        )
        return result
