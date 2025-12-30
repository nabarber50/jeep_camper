# cam/setup/foamcam/cam_ops.py
import adsk.core, adsk.cam, adsk.fusion
import math
from typing import List
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

    def _find_tool_by_name(self, name_substring: str, library_url: str = None):
        """Search tool library for a tool whose description/name contains the given substring.
        
        Uses official Autodesk API pattern: CAMManager.get() -> libraryManager -> toolLibraries
        """
        if not name_substring:
            self.logger.log("Tool search: no name substring provided")
            return None
        
        search_key = name_substring.lower()
        self.logger.log(f"Tool search: looking for '{search_key}'")
        
        try:
            # Official pattern from Autodesk sample code
            camManager = adsk.cam.CAMManager.get()
            if not camManager:
                self.logger.log("Tool search: CAMManager.get() returned None")
                return None
            
            libraryManager = camManager.libraryManager
            if not libraryManager:
                self.logger.log("Tool search: libraryManager is None")
                return None
            
            toolLibraries = libraryManager.toolLibraries
            if not toolLibraries:
                self.logger.log("Tool search: toolLibraries is None")
                return None
            
            # If specific library URL provided, load and search it
            if library_url:
                try:
                    url = adsk.core.URL.create(library_url)
                    lib = toolLibraries.toolLibraryAtURL(url)
                    if lib:
                        lib_name = getattr(lib, 'name', library_url)
                        self.logger.log(f"  Loaded library: {lib_name}")
                        tools = getattr(lib, "tools", None)
                        if tools:
                            for ti in range(tools.count):
                                try:
                                    tool = tools.item(ti)
                                    desc = getattr(tool, "description", "")
                                    name = getattr(tool, "name", "")
                                    if search_key in desc.lower() or search_key in name.lower():
                                        self.logger.log(f"Tool search: Found '{desc or name}'")
                                        return tool
                                except Exception as e:
                                    if ti < 3:
                                        self.logger.log(f"    Tool[{ti}]: error - {e}")
                except Exception as e:
                    self.logger.log(f"  Failed to load library '{library_url}': {e}")
                    return None  # Stop here if specific library search fails
            
            # If no library URL or not found in specified library, we can't search all
            # because toolLibraries doesn't support iteration in script context
            self.logger.log(f"Tool search: no match for '{search_key}' in library")
            return None
        
        except Exception as e:
            self.logger.log(f"Tool search: exception - {e}")
            return None
    
    def _find_tool_best_effort(self):
        """Find tool using config-specified name or diameter.
        
        Uses official Autodesk CAM API patterns for tool library access.
        """
        try:
            library_url = getattr(self.Config, 'TOOL_LIBRARY_URL', None)
            if not library_url:
                self.logger.log("Tool auto-pick: no TOOL_LIBRARY_URL in config")
                return None
            
            # Try name search first if configured
            name_search = getattr(self.Config, 'TOOL_NAME_SEARCH', None)
            if name_search:
                self.logger.log(f"Tool auto-pick: searching by name '{name_search}'")
                tool = self._find_tool_by_name(name_search, library_url)
                if tool:
                    return tool
            
            # Fall back to query by criteria
            tool_type = getattr(self.Config, 'TOOL_TYPE', 'flat end mill')
            min_diam = getattr(self.Config, 'TOOL_DIAMETER_MIN', 0.3)
            max_diam = getattr(self.Config, 'TOOL_DIAMETER_MAX', 0.6)
            min_flute = getattr(self.Config, 'TOOL_MIN_FLUTE_LENGTH', None)
            
            self.logger.log(f"Tool auto-pick: query by criteria")
            return self._find_tool_by_query(library_url, tool_type, min_diam, max_diam, min_flute)
            
        except Exception as e:
            self.logger.log(f"Tool auto-pick: exception - {e}")
            return None
    
    def _find_tool_by_query(self, library_url: str, tool_type: str, min_diameter: float, max_diameter: float, min_flute_length: float = None):
        """Find tool using query criteria (diameter, type, flute length).
        
        Uses official Autodesk API pattern from sample code with ToolQuery.
        Parameters in cm (Fusion internal units).
        """
        try:
            camManager = adsk.cam.CAMManager.get()
            if not camManager:
                self.logger.log("Tool query: CAMManager.get() returned None")
                return None
            
            libraryManager = camManager.libraryManager
            if not libraryManager:
                self.logger.log("Tool query: libraryManager is None")
                return None
            
            toolLibraries = libraryManager.toolLibraries
            if not toolLibraries:
                self.logger.log("Tool query: toolLibraries is None")
                return None
            
            # Load the specified library by URL
            try:
                url = adsk.core.URL.create(library_url)
                toolLibrary = toolLibraries.toolLibraryAtURL(url)
            except Exception as e:
                self.logger.log(f"Tool query: Failed to load library '{library_url}': {e}")
                return None
            
            if not toolLibrary:
                self.logger.log(f"Tool query: toolLibrary is None for '{library_url}'")
                return None
            
            try:
                lib_name = toolLibrary.name
            except:
                lib_name = library_url
            self.logger.log(f"Tool query: Loaded library '{lib_name}'")
            
            # Create query with criteria (official pattern from Autodesk sample)
            try:
                query = toolLibrary.createQuery()
            except Exception as e:
                self.logger.log(f"Tool query: createQuery() failed: {e}")
                return None
            
            # Add criteria using ValueInput objects (official pattern)
            try:
                query.criteria.add('tool_type', adsk.core.ValueInput.createByString(tool_type))
                query.criteria.add('tool_diameter.min', adsk.core.ValueInput.createByReal(min_diameter))
                query.criteria.add('tool_diameter.max', adsk.core.ValueInput.createByReal(max_diameter))
                
                if min_flute_length is not None:
                    query.criteria.add('tool_fluteLength.min', adsk.core.ValueInput.createByReal(min_flute_length))
            except Exception as e:
                self.logger.log(f"Tool query: Failed to set criteria: {e}")
                return None
            
            criteria_str = f"type='{tool_type}' diameter={min_diameter:.2f}-{max_diameter:.2f}cm"
            if min_flute_length:
                criteria_str += f" flute_length>={min_flute_length:.2f}cm"
            self.logger.log(f"  Criteria: {criteria_str}")
            
            # Execute query and extract tools from results (per official pattern)
            try:
                results = query.execute()
            except Exception as e:
                self.logger.log(f"Tool query: execute() failed: {e}")
                return None
            
            if not results:
                self.logger.log(f"Tool query: execute() returned None")
                return None
            
            # results is a list of objects with .tool property
            tools_found = []
            for result in results:
                try:
                    tool = result.tool
                    tools_found.append(tool)
                except Exception as e:
                    self.logger.log(f"  Warning: Could not extract tool from result: {e}")
            
            if tools_found:
                tool = tools_found[0]
                try:
                    tool_desc = tool.description
                except:
                    tool_desc = "(unknown)"
                self.logger.log(f"  Success: Found '{tool_desc}' ({len(tools_found)} total matches)")
                return tool
            else:
                self.logger.log(f"  No tools matched the query criteria")
                return None
                
        except Exception as e:
            self.logger.log(f"Tool query: Unexpected exception - {e}")
            import traceback
            self.logger.log(f"  Traceback: {traceback.format_exc()}")
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

    def _auto_select_contours_on_operation(
        self,
        operation: 'adsk.cam.Operation',
        model_bodies: List['adsk.fusion.BRepBody']
    ) -> None:
        """Select contours for 2D Profile operation after it's been added to setup."""
        try:
            self.logger.log(f"  Contour selection: checking parameters on added operation...")
            params = operation.parameters
            
            # List available parameters for debugging
            param_names = []
            for i in range(params.count):
                param = params.item(i)
                param_names.append(param.name)
            self.logger.log(f"    Available params on added op: {', '.join(param_names[:20])}")
            
            # Try to find the contours parameter
            contours_param_name = None
            for name in ['contours', 'machiningBoundarySel', 'geometry', 'silhouette']:
                if params.itemByName(name):
                    contours_param_name = name
                    self.logger.log(f"    Found contours parameter: '{name}'")
                    break
            
            if not contours_param_name:
                self.logger.log(f"  WARNING: No contours parameter found")
                return
                
            cadcontours2d_param = params.itemByName(contours_param_name).value
            if not cadcontours2d_param:
                self.logger.log(f"  Contour parameter '{contours_param_name}' has no value")
                return

            chains = cadcontours2d_param.getCurveSelections()
            if chains is None:
                self.logger.log(f"  getCurveSelections() returned None")
                return

            # Try face contour selection first
            self.logger.log(f"  Attempting face contour selection...")
            for body in model_bodies:
                for face in body.faces:
                    try:
                        chain = chains.createNewChainSelection()
                        chain.inputGeometry = [face]
                        cadcontours2d_param.applyCurveSelections(chains)
                        self.logger.log(f"    SUCCESS: Selected face contour")
                        return
                    except:
                        pass

            # Fallback: edge selection
            self.logger.log(f"  Face selection failed, trying edge selection...")
            edges = []
            for body in model_bodies:
                for edge in body.edges:
                    edges.append(edge)

            if edges:
                chain = chains.createNewChainSelection()
                chain.inputGeometry = edges
                cadcontours2d_param.applyCurveSelections(chains)
                self.logger.log(f"    Edge selection applied ({len(edges)} edges)")
            else:
                self.logger.log(f"  WARNING: No edges found in model")

        except Exception as e:
            self.logger.log(f"  Contour selection error: {str(e)}")

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

    def create_for_sheets(self, sheets, ui, rotate_sheets: bool = None, rotate_wcs: bool = None, swap_xy: bool = None) -> CamBuildResult:
        """
        Create CAM setups for each sheet layout.
        
        Parameters:
          rotate_sheets: override MASLOW_ROTATE_SHEET_BODIES (None = use Config)
          rotate_wcs: override auto WCS rotation in enforcer (None = auto-decide)
          swap_xy: override MASLOW_SWAP_XY_COMPENSATION (None = use Config)
        """
        warn_state = {"warned": False}
        tool = None

        self.logger.log("=== Tool selection starting ===")
        try:
            self.logger.log("Calling _find_tool_best_effort...")
            tool = self._find_tool_best_effort()
            if tool:
                try:
                    tool_desc = tool.description
                except:
                    tool_desc = "(unknown)"
                self.logger.log(f"Tool auto-picked: {tool_desc}")
            else:
                self.logger.log("Tool auto-pick returned None; ops will be created without tool selection.")
        except Exception as e:
            self.logger.log(f"Tool auto-pick exception: {e}")
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
            # Respect parameter overrides first, then Config.
            caller_swap = swap_xy if swap_xy is not None else getattr(self.Config, 'MASLOW_SWAP_XY_COMPENSATION', None)
            caller_rotate = rotate_sheets if rotate_sheets is not None else getattr(self.Config, 'MASLOW_ROTATE_SHEET_BODIES', None)
            if caller_swap is True and caller_rotate is True:
                self.logger.log(f'CALLSITE: invoking rotation: MASLOW_SWAP_XY_COMPENSATION={caller_swap} MASLOW_ROTATE_SHEET_BODIES={caller_rotate} (overrides: swap_xy={swap_xy} rotate_sheets={rotate_sheets})')
                self._apply_xy_swap_compensation_rotation(occ.component)
            else:
                self.logger.log(f'CALLSITE: skipping rotation: MASLOW_SWAP_XY_COMPENSATION={caller_swap} MASLOW_ROTATE_SHEET_BODIES={caller_rotate} (overrides: swap_xy={swap_xy} rotate_sheets={rotate_sheets})')

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
                    self.enforcer.enforce(setup, model_bodies, force_wcs_rotation=rotate_wcs, force_swap_xy=swap_xy)
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
                
                # Add operation first
                prof = ops.add(prof_in)
                try_assign_tool(prof)
                
                # Auto-select contours from model bodies (on ADDED operation, not input)
                self._auto_select_contours_on_operation(prof, model_bodies)

                self._set_bool(prof.parameters, 'doRoughingPasses', True)
                self._set_bool(prof.parameters, 'doMultipleDepths', True)
                self._set_expr(prof.parameters, 'maximumStepdown', self.Config.PROFILE_STEPDOWN)

                self._apply_maslow_z(prof)

            # 3D adaptive
            try:
                rough_in = ops.createInput('adaptive')
                rough_in.displayName = 'Foam Rough 3D (Adaptive)'
                # Explicitly set model geometry to setup models
                try:
                    rough_in.models = setup.models
                except:
                    pass
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
                # Explicitly set model geometry to setup models
                try:
                    fin_in.models = setup.models
                except:
                    pass
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
