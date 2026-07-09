# -*- coding: utf-8 -*-

"""
QGIS Processing Algorithm: H3 Grid Layer Evaluation Metrics (K-Ring Adjacency Version)
Author: Antigravity
Description: Evaluates an Event Detection layer against a Ground Truth layer, 
both consisting of H3 grid cells of the same resolution.
Computes exact index-based metrics (Precision, Recall, F1, Jaccard), 
universe-based metrics (Accuracy, Specificity, MCC, Kappa) if a base grid is provided, 
and H3 grid-disk (K-Ring) neighbor-tolerant soft metrics if a grid tolerance > 0 is set.
Outputs a classified overlay layer and a premium styled HTML report.
"""

import math
import os
import sys
from PyQt5.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsSpatialIndex,
    QgsGeometry,
    QgsFeatureSink,
    NULL
)

class H3GridEvaluationMetricsKRing(QgsProcessingAlgorithm):
    # Parameter identifiers
    DETECTION_LAYER = 'DETECTION_LAYER'
    DETECTION_FIELD = 'DETECTION_FIELD'
    GT_LAYER = 'GT_LAYER'
    GT_FIELD = 'GT_FIELD'
    BASE_GRID_LAYER = 'BASE_GRID_LAYER'
    BASE_GRID_FIELD = 'BASE_GRID_FIELD'
    GRID_TOLERANCE = 'GRID_TOLERANCE'
    USE_CASE_NAME = 'USE_CASE_NAME'
    DISASTER_TYPE = 'DISASTER_TYPE'
    DISASTER_DATE = 'DISASTER_DATE'
    OUTPUT_LAYER = 'OUTPUT_LAYER'
    OUTPUT_REPORT = 'OUTPUT_REPORT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return H3GridEvaluationMetricsKRing()

    def name(self):
        return 'h3_grid_evaluation_metrics_kring'

    def displayName(self):
        return self.tr('H3 Grid Layer Evaluation Metrics (K-Ring)')

    def group(self):
        return self.tr('Social Media Analysis')

    def groupId(self):
        return 'socialmediaanalysis'

    def shortHelpString(self):
        return self.tr(
            "Evaluates the performance of an Event Detection layer against a Ground Truth layer using H3 grid adjacency.\n\n"
            "Both input layers must consist of H3 grid cells of the same resolution.\n\n"
            "Calculated Metrics:\n"
            "1. Exact Match Metrics: Precision, Recall, F1-Score, Jaccard Index (IoU).\n"
            "2. Universe-Dependent Metrics (if Base Grid is provided): Accuracy, Specificity, Matthews Correlation Coefficient (MCC), Cohen's Kappa.\n"
            "3. Grid Disk (K-Ring) Soft Metrics (if Grid Tolerance > 0): Tolerant metrics using H3 hexagon grid steps instead of geometry buffer distances.\n\n"
            "Outputs:\n"
            "- Classified Overlay Layer: Categorized features showing TP, FP, FN, and TN.\n"
            "- Evaluation Report: A beautifully styled HTML file showing detailed metrics table and equations."
        )

    def initAlgorithm(self, config=None):
        # 1. Detection Layer (e.g. H3 of Event detected tweets)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.DETECTION_LAYER,
                self.tr('Event Detection Layer (Prediction)'),
                [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.DETECTION_FIELD,
                self.tr('H3 Index Field (Detection Layer)'),
                parentLayerParameterName=self.DETECTION_LAYER,
                type=QgsProcessingParameterField.Any,
                defaultValue='index'
            )
        )

        # 2. Ground Truth Layer (e.g. H3 of disaster related tweets)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.GT_LAYER,
                self.tr('Ground Truth Layer (Reference)'),
                [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.GT_FIELD,
                self.tr('H3 Index Field (Ground Truth Layer)'),
                parentLayerParameterName=self.GT_LAYER,
                type=QgsProcessingParameterField.Any,
                defaultValue='index'
            )
        )

        # 3. Base Grid Layer (Optional, e.g. H3 of all tweets)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.BASE_GRID_LAYER,
                self.tr('Base Grid Layer (Universe - Optional)'),
                [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.BASE_GRID_FIELD,
                self.tr('H3 Index Field (Base Grid Layer)'),
                parentLayerParameterName=self.BASE_GRID_LAYER,
                type=QgsProcessingParameterField.Any,
                defaultValue='index',
                optional=True
            )
        )

        # 4. Grid Tolerance (for neighbor-aware soft matching via K-Ring)
        self.addParameter(
            QgsProcessingParameterNumber(
                self.GRID_TOLERANCE,
                self.tr('Grid Tolerance (K-Ring radius in hexagon steps, 0 for exact match only)'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0
            )
        )

        # 4b. Use Case Information
        self.addParameter(
            QgsProcessingParameterString(
                self.USE_CASE_NAME,
                self.tr('Use Case Name'),
                defaultValue='Disaster Detection Use Case'
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.DISASTER_TYPE,
                self.tr('Type of Disaster'),
                defaultValue='Flood'
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.DISASTER_DATE,
                self.tr('Date of Disaster / Event'),
                defaultValue='2026-06-22'
            )
        )

        # 5. Output Vector Layer (Classification Overlay)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr('Classified Overlay Layer')
            )
        )

        # 6. Output HTML Report
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_REPORT,
                self.tr('Evaluation Report (HTML)'),
                fileFilter='HTML files (*.html)'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        # Import H3 library and handle version dynamically
        try:
            import h3
        except ImportError:
            raise QgsProcessingException("The 'h3' Python library is required but was not found. Please install it using pip.")
            
        h3_ver = getattr(h3, '__version__', '3.x')
        is_v4 = h3_ver.startswith('4')
        feedback.pushInfo(f"Loaded H3 Python library version: {h3_ver} (v4 API: {is_v4})")

        # Define grid disk retrieval based on API version
        def get_grid_disk(cell, k):
            try:
                # String indexes must match cases
                c = cell.strip()
                if is_v4:
                    return h3.grid_disk(c, k)
                else:
                    return h3.k_ring(c, k)
            except Exception as e:
                feedback.pushWarning(f"Error calculating grid disk for cell {cell}: {e}")
                return {cell}

        # Retrieve sources
        det_source = self.parameterAsSource(parameters, self.DETECTION_LAYER, context)
        gt_source = self.parameterAsSource(parameters, self.GT_LAYER, context)
        base_source = self.parameterAsSource(parameters, self.BASE_GRID_LAYER, context)

        if det_source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.DETECTION_LAYER))
        if gt_source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.GT_LAYER))

        det_field = self.parameterAsString(parameters, self.DETECTION_FIELD, context)
        gt_field = self.parameterAsString(parameters, self.GT_FIELD, context)
        base_field = self.parameterAsString(parameters, self.BASE_GRID_FIELD, context)
        grid_k = self.parameterAsInt(parameters, self.GRID_TOLERANCE, context)
        use_case = self.parameterAsString(parameters, self.USE_CASE_NAME, context)
        disaster_type = self.parameterAsString(parameters, self.DISASTER_TYPE, context)
        disaster_date = self.parameterAsString(parameters, self.DISASTER_DATE, context)

        # Verify fields in source layers
        if det_field not in det_source.fields().names():
            raise QgsProcessingException(f"Field '{det_field}' not found in Detection Layer.")
        if gt_field not in gt_source.fields().names():
            raise QgsProcessingException(f"Field '{gt_field}' not found in Ground Truth Layer.")
        if base_source is not None and base_field not in base_source.fields().names():
            raise QgsProcessingException(f"Field '{base_field}' not found in Base Grid Layer.")

        feedback.setProgressText("Loading features into memory...")

        # Load Detection features (keys normalized to lowercase)
        det_features = {}
        for feat in det_source.getFeatures():
            val = feat[det_field]
            if val is not None and val != NULL:
                idx_str = str(val).strip().lower()
                if idx_str:
                    det_features[idx_str] = QgsFeature(feat)

        # Load Ground Truth features
        gt_features = {}
        for feat in gt_source.getFeatures():
            val = feat[gt_field]
            if val is not None and val != NULL:
                idx_str = str(val).strip().lower()
                if idx_str:
                    gt_features[idx_str] = QgsFeature(feat)

        # Load Base Grid features if available
        base_features = {}
        if base_source is not None:
            for feat in base_source.getFeatures():
                val = feat[base_field]
                if val is not None and val != NULL:
                    idx_str = str(val).strip().lower()
                    if idx_str:
                        base_features[idx_str] = QgsFeature(feat)

        det_indices = set(det_features.keys())
        gt_indices = set(gt_features.keys())
        base_indices = set(base_features.keys())

        # Exact classification mapping
        tp_indices = det_indices & gt_indices
        fp_indices = det_indices - gt_indices
        fn_indices = gt_indices - det_indices
        
        # Universe classification
        has_base = len(base_indices) > 0
        if has_base:
            universe = base_indices | det_indices | gt_indices
            tn_indices = universe - (det_indices | gt_indices)
        else:
            universe = det_indices | gt_indices
            tn_indices = set()

        tp_count = len(tp_indices)
        fp_count = len(fp_indices)
        fn_count = len(fn_indices)
        tn_count = len(tn_indices)

        feedback.pushInfo(f"Exact Matches: TP={tp_count}, FP={fp_count}, FN={fn_count}, TN={tn_count}")

        # Compute Exact Metrics
        precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0.0
        recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0.0
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        jaccard = tp_count / (tp_count + fp_count + fn_count) if (tp_count + fp_count + fn_count) > 0 else 0.0

        exact_metrics = {
            'tp': tp_count,
            'fp': fp_count,
            'fn': fn_count,
            'tn': tn_count if has_base else None,
            'precision': precision,
            'recall': recall,
            'f1': f1_score,
            'jaccard': jaccard
        }

        # Universe-dependent metrics
        universe_metrics = None
        if has_base:
            total_cells = tp_count + tn_count + fp_count + fn_count
            accuracy = (tp_count + tn_count) / total_cells if total_cells > 0 else 0.0
            specificity = tn_count / (tn_count + fp_count) if (tn_count + fp_count) > 0 else 0.0
            fpr = fp_count / (tn_count + fp_count) if (tn_count + fp_count) > 0 else 0.0
            
            # MCC calculation
            mcc_num = (tp_count * tn_count) - (fp_count * fn_count)
            mcc_den = math.sqrt(float(tp_count + fp_count) * (tp_count + fn_count) * (tn_count + fp_count) * (tn_count + fn_count))
            mcc = mcc_num / mcc_den if mcc_den > 0 else 0.0

            # Cohen's Kappa calculation
            p_o = (tp_count + tn_count) / total_cells if total_cells > 0 else 0.0
            p_yes = (float(tp_count + fp_count) * (tp_count + fn_count)) / (total_cells * total_cells) if total_cells > 0 else 0.0
            p_no = (float(tn_count + fp_count) * (tn_count + fn_count)) / (total_cells * total_cells) if total_cells > 0 else 0.0
            p_e = p_yes + p_no
            kappa = (p_o - p_e) / (1.0 - p_e) if (1.0 - p_e) > 0 else 0.0

            universe_metrics = {
                'accuracy': accuracy,
                'specificity': specificity,
                'fpr': fpr,
                'mcc': mcc,
                'kappa': kappa,
                'total': total_cells
            }

        # H3 K-Ring Soft Metrics
        soft_metrics = None
        gt_expanded = set()
        det_expanded = set()

        if grid_k > 0:
            feedback.setProgressText(f"Expanding H3 grids by K-Ring disk of radius {grid_k}...")
            
            # Expand Ground Truth set
            for idx_str in gt_indices:
                gt_expanded.update(get_grid_disk(idx_str, grid_k))
            # Normalize expanded index strings to lowercase
            gt_expanded = {x.lower() for x in gt_expanded}
                
            # Expand Detection set
            for idx_str in det_indices:
                det_expanded.update(get_grid_disk(idx_str, grid_k))
            det_expanded = {x.lower() for x in det_expanded}

            # Evaluate Soft Matches
            soft_tp_det_count = sum(1 for idx in det_indices if idx in gt_expanded)
            soft_tp_gt_count = sum(1 for idx in gt_indices if idx in det_expanded)

            soft_precision = soft_tp_det_count / len(det_indices) if len(det_indices) > 0 else 0.0
            soft_recall = soft_tp_gt_count / len(gt_indices) if len(gt_indices) > 0 else 0.0
            soft_f1 = 2 * (soft_precision * soft_recall) / (soft_precision + soft_recall) if (soft_precision + soft_recall) > 0 else 0.0

            soft_metrics = {
                'tp_det': soft_tp_det_count,
                'fp_det': len(det_indices) - soft_tp_det_count,
                'tp_gt': soft_tp_gt_count,
                'fn_gt': len(gt_indices) - soft_tp_gt_count,
                'precision': soft_precision,
                'recall': soft_recall,
                'f1': soft_f1
            }
            feedback.pushInfo(f"H3 K-Ring Matches (k={grid_k}): Soft TP (Det)={soft_tp_det_count}, Soft TP (GT)={soft_tp_gt_count}")

        # Create output layer fields
        out_fields = QgsFields()
        out_fields.append(QgsField('h3_index', QVariant.String))
        out_fields.append(QgsField('eval_status', QVariant.String))
        out_fields.append(QgsField('in_det', QVariant.Int))
        out_fields.append(QgsField('in_gt', QVariant.Int))
        out_fields.append(QgsField('in_base', QVariant.Int))
        out_fields.append(QgsField('is_soft_tp', QVariant.Int))

        # Setup feature sink (using detection layer's crs and geometry type)
        crs = det_source.sourceCrs()
        wkb_type = det_source.wkbType()
        
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_LAYER,
            context,
            out_fields,
            wkb_type,
            crs
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT_LAYER))

        feedback.setProgressText("Writing classified overlay features...")
        
        # Write union features to output sink
        write_indices = universe
        total_write = len(write_indices)
        
        for i, idx_str in enumerate(write_indices):
            if feedback.isCanceled():
                break

            out_feat = QgsFeature()
            
            # Select best geometry available
            geom = None
            if idx_str in det_features:
                geom = det_features[idx_str].geometry()
            elif idx_str in gt_features:
                geom = gt_features[idx_str].geometry()
            elif idx_str in base_features:
                geom = base_features[idx_str].geometry()
            
            if geom is not None:
                out_feat.setGeometry(geom)
            
            out_feat.setFields(out_fields)
            
            in_det = 1 if idx_str in det_indices else 0
            in_gt = 1 if idx_str in gt_indices else 0
            in_base = 1 if idx_str in base_indices else 0
            
            # Classification status
            if in_det == 1 and in_gt == 1:
                status = 'TP'
            elif in_det == 1 and in_gt == 0:
                status = 'FP'
            elif in_det == 0 and in_gt == 1:
                status = 'FN'
            else:
                status = 'TN'
                
            # Soft TP status (1 if exact TP, or if the cell falls inside expanded buffer of the other layer)
            is_soft = 0
            if grid_k > 0:
                if status == 'TP':
                    is_soft = 1
                elif status == 'FP' and idx_str in gt_expanded:
                    is_soft = 1
                elif status == 'FN' and idx_str in det_expanded:
                    is_soft = 1
            
            out_feat.setAttribute('h3_index', idx_str)
            out_feat.setAttribute('eval_status', status)
            out_feat.setAttribute('in_det', in_det)
            out_feat.setAttribute('in_gt', in_gt)
            out_feat.setAttribute('in_base', in_base)
            out_feat.setAttribute('is_soft_tp', is_soft)
            
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
            
            if i % 1000 == 0:
                feedback.setProgress(int((i / total_write) * 100))

        # Generate HTML report
        report_path = self.parameterAsFileOutput(parameters, self.OUTPUT_REPORT, context)
        self.write_html_report(
            report_path, exact_metrics, universe_metrics, soft_metrics, grid_k,
            use_case, disaster_type, disaster_date
        )

        return {
            self.OUTPUT_LAYER: dest_id,
            self.OUTPUT_REPORT: report_path
        }

    def write_html_report(self, path, exact, universe, soft, grid_k, use_case, disaster_type, disaster_date):
        # Create HTML content with a premium slate-blue/dark interface
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>H3 Evaluation Metrics Report</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap');
        
        :root {{
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --border-color: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-blue: #3b82f6;
            --accent-cyan: #06b6d4;
            --accent-green: #10b981;
            --accent-purple: #8b5cf6;
            --accent-red: #ef4444;
            --card-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -4px rgba(0, 0, 0, 0.3);
        }}
        
        body {{
            background-color: var(--bg-primary);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            margin: 0;
            padding: 40px 20px;
            display: flex;
            justify-content: center;
        }}
        
        .container {{
            width: 100%;
            max-width: 1100px;
        }}
        
        header {{
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: var(--card-shadow);
            position: relative;
            overflow: hidden;
        }}
        
        header::after {{
            content: '';
            position: absolute;
            top: 0;
            right: 0;
            width: 300px;
            height: 300px;
            background: radial-gradient(circle, rgba(59, 130, 246, 0.1) 0%, rgba(0, 0, 0, 0) 70%);
            z-index: 1;
        }}
        
        h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.5rem;
            font-weight: 700;
            margin: 0 0 10px 0;
            background: linear-gradient(to right, var(--text-primary), var(--accent-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        
        p.subtitle {{
            color: var(--text-secondary);
            font-size: 1.1rem;
            margin: 0;
        }}

        .metadata-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
            margin-top: 20px;
            padding-top: 15px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            font-size: 0.95rem;
            color: var(--text-secondary);
            z-index: 2;
            position: relative;
        }}
        
        .metadata-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        
        .metadata-item strong {{
            color: var(--text-primary);
            font-weight: 600;
        }}
        
        .grid-3 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .card {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            box-shadow: var(--card-shadow);
            transition: transform 0.2s, border-color 0.2s;
        }}
        
        .card:hover {{
            transform: translateY(-2px);
            border-color: #475569;
        }}
        
        .card-title {{
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-weight: 600;
        }}
        
        .card-value {{
            font-size: 2.2rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1;
            margin-bottom: 8px;
            font-family: 'Outfit', sans-serif;
        }}
        
        .card-desc {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin: 0;
        }}
        
        .metric-badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
            margin-top: 10px;
        }}
        
        .badge-blue {{ background-color: rgba(59, 130, 246, 0.15); color: var(--accent-blue); }}
        .badge-green {{ background-color: rgba(16, 185, 129, 0.15); color: var(--accent-green); }}
        .badge-purple {{ background-color: rgba(139, 92, 246, 0.15); color: var(--accent-purple); }}
        .badge-cyan {{ background-color: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); }}
        
        .section-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.5rem;
            font-weight: 600;
            margin: 40px 0 20px 0;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 8px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            background-color: var(--bg-secondary);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: var(--card-shadow);
            border: 1px solid var(--border-color);
            margin-bottom: 30px;
        }}
        
        th {{
            background-color: #1e293b;
            padding: 16px;
            text-align: left;
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-secondary);
            border-bottom: 2px solid var(--border-color);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        td {{
            padding: 16px;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.95rem;
            color: var(--text-primary);
        }}
        
        tr:last-child td {{
            border-bottom: none;
        }}
        
        tr:hover td {{
            background-color: rgba(255, 255, 255, 0.02);
        }}
        
        .text-right {{
            text-align: right;
        }}
        
        .formula {{
            font-family: monospace;
            background-color: rgba(0, 0, 0, 0.2);
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.9rem;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }}
        
        .number-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        
        .stat-item {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }}
        
        .stat-num {{
            font-size: 1.8rem;
            font-weight: 700;
            margin-bottom: 4px;
            font-family: 'Outfit', sans-serif;
        }}
        
        .stat-label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 500;
        }}
        
        .tp-color {{ color: var(--accent-green); }}
        .fp-color {{ color: var(--accent-red); }}
        .fn-color {{ color: var(--accent-purple); }}
        .tn-color {{ color: var(--accent-blue); }}
        
        .qgis-tip {{
            background: rgba(59, 130, 246, 0.05);
            border-left: 4px solid var(--accent-blue);
            padding: 16px;
            border-radius: 0 12px 12px 0;
            margin-top: 30px;
            font-size: 0.9rem;
            line-height: 1.5;
            color: var(--text-secondary);
        }}
        
        .qgis-tip-title {{
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>H3 Grid Layer Evaluation Report</h1>
            <p class="subtitle">Quantitative metrics comparing Event Detection and Ground Truth layers</p>
            <div class="metadata-grid">
                <div class="metadata-item"><strong>Use Case:</strong> {use_case}</div>
                <div class="metadata-item"><strong>Disaster:</strong> {disaster_type}</div>
                <div class="metadata-item"><strong>Date:</strong> {disaster_date}</div>
            </div>
        </header>

        <!-- Count Statistics -->
        <div class="number-grid">
            <div class="stat-item">
                <div class="stat-num tp-color">{exact['tp']}</div>
                <div class="stat-label">True Positives (TP)</div>
            </div>
            <div class="stat-item">
                <div class="stat-num fp-color">{exact['fp']}</div>
                <div class="stat-label">False Positives (FP)</div>
            </div>
            <div class="stat-item">
                <div class="stat-num fn-color">{exact['fn']}</div>
                <div class="stat-label">False Negatives (FN)</div>
            </div>
            <div class="stat-item">
                <div class="stat-num tn-color">{"N/A" if exact['tn'] is None else exact['tn']}</div>
                <div class="stat-label">True Negatives (TN)</div>
            </div>
        </div>

        <!-- Primary KPI Cards -->
        <div class="grid-3">
            <div class="card">
                <div class="card-title">Precision</div>
                <div class="card-value">{exact['precision']:.2%}</div>
                <p class="card-desc">Proportion of detected events that are actual ground truth. High precision means low false alarms.</p>
                <div class="metric-badge badge-blue">Exact Index Match</div>
            </div>
            <div class="card">
                <div class="card-title">Recall</div>
                <div class="card-value">{exact['recall']:.2%}</div>
                <p class="card-desc">Proportion of actual events successfully detected. High recall means low missed events.</p>
                <div class="metric-badge badge-green">Exact Index Match</div>
            </div>
            <div class="card">
                <div class="card-title">F1-Score</div>
                <div class="card-value">{exact['f1']:.2%}</div>
                <p class="card-desc">Balanced harmonic mean of Precision and Recall. Best overall binary metric.</p>
                <div class="metric-badge badge-purple">Exact Index Match</div>
            </div>
        </div>
"""

        if soft is not None:
            html += f"""
        <!-- H3 K-Ring Soft KPI Cards -->
        <div class="section-title">H3 Grid Disk Soft Metrics (K-Ring = {grid_k})</div>
        <p style="color: var(--text-secondary); margin-bottom: 20px; font-size: 0.95rem; line-height: 1.5;">
            Calculated by allowing an H3 grid disk step tolerance of <strong>{grid_k}</strong> hexagons (K-Ring). 
            A detection cell is classified as a Soft TP if it lies within {grid_k} grid steps of any Ground Truth cell (and vice versa). 
            This matches spatial adjacencies natively using the H3 topology without coordinate system distortions.
        </p>
        
        <div class="number-grid" style="grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));">
            <div class="stat-item">
                <div class="stat-num tp-color">{soft['tp_det']} / {exact['tp'] + exact['fp']}</div>
                <div class="stat-label">Soft TP (Detections within K-Ring of GT)</div>
            </div>
            <div class="stat-item">
                <div class="stat-num tp-color">{soft['tp_gt']} / {exact['tp'] + exact['fn']}</div>
                <div class="stat-label">Soft TP (GT within K-Ring of Detections)</div>
            </div>
        </div>

        <div class="grid-3">
            <div class="card">
                <div class="card-title">Soft Precision</div>
                <div class="card-value">{soft['precision']:.2%}</div>
                <p class="card-desc">Detected cells falling within the {grid_k}-ring of at least one ground truth cell.</p>
                <div class="metric-badge badge-blue">H3 Grid Adjacency</div>
            </div>
            <div class="card">
                <div class="card-title">Soft Recall</div>
                <div class="card-value">{soft['recall']:.2%}</div>
                <p class="card-desc">Ground truth cells falling within the {grid_k}-ring of at least one detection cell.</p>
                <div class="metric-badge badge-green">H3 Grid Adjacency</div>
            </div>
            <div class="card">
                <div class="card-title">Soft F1-Score</div>
                <div class="card-value">{soft['f1']:.2%}</div>
                <p class="card-desc">Harmonic mean of Soft Precision and Soft Recall.</p>
                <div class="metric-badge badge-purple">H3 Grid Adjacency</div>
            </div>
        </div>
"""

        html += """
        <!-- Detailed Metrics Table -->
        <div class="section-title">Evaluation Metrics Table</div>
        <table>
            <thead>
                <tr>
                    <th>Metric</th>
                    <th>Value</th>
                    <th>Formula</th>
                    <th>Interpretation</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Precision</strong></td>
                    <td>""" + f"{exact['precision']:.4f}" + """</td>
                    <td><span class="formula">TP / (TP + FP)</span></td>
                    <td>Proportion of detected events that are actual events.</td>
                </tr>
                <tr>
                    <td><strong>Recall (Sensitivity)</strong></td>
                    <td>""" + f"{exact['recall']:.4f}" + """</td>
                    <td><span class="formula">TP / (TP + FN)</span></td>
                    <td>Proportion of actual events that were detected.</td>
                </tr>
                <tr>
                    <td><strong>F1-Score</strong></td>
                    <td>""" + f"{exact['f1']:.4f}" + """</td>
                    <td><span class="formula">2 * (P * R) / (P + R)</span></td>
                    <td>Harmonic mean of precision and recall.</td>
                </tr>
                <tr>
                    <td><strong>Jaccard Index (IoU)</strong></td>
                    <td>""" + f"{exact['jaccard']:.4f}" + """</td>
                    <td><span class="formula">TP / (TP + FP + FN)</span></td>
                    <td>Intersection over Union of the active cell sets.</td>
                </tr>
        """

        if universe is not None:
            html += f"""
                <tr>
                    <td><strong>Accuracy</strong></td>
                    <td>{universe['accuracy']:.4f}</td>
                    <td><span class="formula">(TP + TN) / Total</span></td>
                    <td>Overall proportion of correctly classified cells.</td>
                </tr>
                <tr>
                    <td><strong>Specificity</strong></td>
                    <td>{universe['specificity']:.4f}</td>
                    <td><span class="formula">TN / (TN + FP)</span></td>
                    <td>Proportion of non-events correctly identified.</td>
                </tr>
                <tr>
                    <td><strong>False Positive Rate (FPR)</strong></td>
                    <td>{universe['fpr']:.4f}</td>
                    <td><span class="formula">FP / (TN + FP)</span></td>
                    <td>Proportion of non-events falsely labeled as events.</td>
                </tr>
                <tr>
                    <td><strong>Matthews Correlation (MCC)</strong></td>
                    <td>{universe['mcc']:.4f}</td>
                    <td><span class="formula">Balanced Correlation [-1, +1]</span></td>
                    <td>Very robust correlation metric for sparse spatial event grids.</td>
                </tr>
                <tr>
                    <td><strong>Cohen's Kappa (&kappa;)</strong></td>
                    <td>{universe['kappa']:.4f}</td>
                    <td><span class="formula">Agreement adjusted for chance</span></td>
                    <td>Degree of classification agreement compared to random guessing.</td>
                </tr>
            """

        html += f"""
            </tbody>
        </table>
        
        <!-- styling tips -->
        <div class="qgis-tip">
            <div class="qgis-tip-title">💡 QGIS Styling Tip</div>
            To visually analyze the results, open the properties of the output layer <strong>Classified Overlay Layer</strong>.
            Set the symbology to <strong>Categorized</strong> and select the <strong>eval_status</strong> field.
            Click <strong>Classify</strong> and style the classes:<br>
            • <strong style="color: var(--accent-green);">TP</strong> (True Positives): Green fill (Success)<br>
            • <strong style="color: var(--accent-red);">FP</strong> (False Positives): Red fill (False alarm/noise)<br>
            • <strong style="color: var(--accent-purple);">FN</strong> (False Negatives): Purple fill (Missed events)<br>
            • <strong style="color: var(--accent-blue);">TN</strong> (True Negatives): Transparent or light grey outline (Baseline correct)
        </div>
    </div>
</body>
</html>
"""

        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
