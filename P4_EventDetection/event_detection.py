# -*- coding: utf-8 -*-

"""
QGIS Processing Algorithm: Event Detection - Method 3 (Getis-Ord Gi*) - Three Outputs
Author: Antigravity
Description: Implements Method 3 for detecting disaster events using raw tweet records.
It dynamically groups records by H3 cell index (hardcoded: 'index'), extracts the year from the date field (hardcoded: 'date'),
identifies disaster tweets using classification labels (hardcoded: 'classification_label'), aggregates baseline and event counts,
runs Getis-Ord Gi* hotspot analysis on the aggregated cells, and evaluates significance thresholds.
Outputs 3 separate layers:
1. Disaster Alerts (aggregated hotspot cells matching significance & outlier filters).
2. All Event Tweets (raw tweet points/polygons from the event year).
3. Disaster Event Tweets (raw tweet points/polygons from the event year matching the disaster label).
"""

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsSpatialIndex,
    QgsGeometry,
    QgsFeatureSink,
    NULL
)
from PyQt5.QtCore import QVariant, QCoreApplication
import math
import statistics

def extract_year(date_val):
    """Robust year extraction using regular expressions.
    Finds any 4-digit year (1900-2099) inside date strings, python datetimes,
    or PyQt5 QDateTime string representations.
    """
    if date_val is None or date_val == NULL:
        return None

    # 1. Try QDate / QDateTime / python datetime objects directly
    try:
        if hasattr(date_val, 'date') and callable(date_val.date):
            qdate = date_val.date()
            if hasattr(qdate, 'year') and callable(qdate.year):
                return qdate.year()
        elif hasattr(date_val, 'year'):
            if callable(date_val.year):
                return date_val.year()
            return date_val.year
    except:
        pass

    # 2. String parsing fallback using Regex
    try:
        date_str = str(date_val).strip()
        if not date_str:
            return None
        import re
        match = re.search(r'\b(20\d{2}|19\d{2})\b', date_str)
        if match:
            return int(match.group(1))
    except:
        pass

    return None

class EventDetectionMethod3ThreeOutputs(QgsProcessingAlgorithm):
    # Parameter identifiers
    INPUT = 'INPUT'
    DISASTER_LABEL = 'DISASTER_LABEL'
    BASELINE_YEAR = 'BASELINE_YEAR'
    EVENT_YEAR = 'EVENT_YEAR'
    Z_THRESHOLD = 'Z_THRESHOLD'
    P_THRESHOLD = 'P_THRESHOLD'
    OUTPUT = 'OUTPUT'
    OUTPUT_EVENT_ALL = 'OUTPUT_EVENT_ALL'
    OUTPUT_EVENT_DISASTER = 'OUTPUT_EVENT_DISASTER'

    def tr(self, string):
        """Helper to translate strings."""
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        """Returns a new instance of the algorithm class."""
        return EventDetectionMethod3ThreeOutputs()

    def name(self):
        """Unique identifier for the algorithm."""
        return 'event_detection_method3_three_outputs'

    def displayName(self):
        """User-friendly name displayed in the Processing Toolbox."""
        return self.tr('Event Detection - Method 3 (Getis-Ord Gi* - 3 Outputs)')

    def group(self):
        """Group name for the algorithm in the toolbox."""
        return self.tr('Social Media Analysis')

    def groupId(self):
        """Unique group ID."""
        return 'socialmediaanalysis'

    def shortHelpString(self):
        """Help description of the algorithm."""
        return self.tr(
            "Implements Method 3 for disaster event detection using H3 grid cells.\n\n"
            "This script dynamically aggregates raw tweets by cell index and year, "
            "identifies disaster tweets using classification labels, and runs the "
            "Getis-Ord Gi* hotspot analysis on unique aggregated cells. "
            "It outputs three separate layers:\n"
            "1. Disaster Alerts: Aggregated cells that trigger alerts (total event count > baseline median, z-score >= threshold, p-value <= threshold).\n"
            "2. All Event Tweets: Raw tweet points/polygons from the event year.\n"
            "3. Disaster Event Tweets: Raw tweet points/polygons from the event year matching the disaster label.\n\n"
            "Assumed schema (automatically mapped):\n"
            "- H3 Index field: 'index'\n"
            "- Date field: 'date'\n"
            "- Classification Label field: 'classification_label'"
        )

    def initAlgorithm(self, config=None):
        """Defines the input and output parameters."""
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr('Input Tweets Layer'),
                [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint]
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.DISASTER_LABEL,
                self.tr('Disaster Label Value'),
                defaultValue='LABEL_1'
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BASELINE_YEAR,
                self.tr('Baseline Year'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=2022
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.EVENT_YEAR,
                self.tr('Event Year'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=2023
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.Z_THRESHOLD,
                self.tr('Z-score Significance Threshold (z >=)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.65
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_THRESHOLD,
                self.tr('P-value Significance Threshold (p <=)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.10
            )
        )
        # Multiple Outputs
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Disaster Alerts (Hotspots)')
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_EVENT_ALL,
                self.tr('All Event Tweets (Event Year)'),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_EVENT_DISASTER,
                self.tr('Disaster Event Tweets (Event Year)'),
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """Executes the event detection algorithm."""
        # 1. Retrieve parameters
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        disaster_label = self.parameterAsString(parameters, self.DISASTER_LABEL, context)
        baseline_year = self.parameterAsInt(parameters, self.BASELINE_YEAR, context)
        event_year = self.parameterAsInt(parameters, self.EVENT_YEAR, context)
        z_threshold = self.parameterAsDouble(parameters, self.Z_THRESHOLD, context)
        p_threshold = self.parameterAsDouble(parameters, self.P_THRESHOLD, context)

        # Hardcoded fields as per input layer schema
        index_field = 'index'
        date_field = 'date'
        label_field = 'classification_label'

        # Verify fields exist in the source layer
        source_fields = [f.name() for f in source.fields()]
        for req_field in [index_field, date_field, label_field]:
            if req_field not in source_fields:
                raise QgsProcessingException(
                    f"Required field '{req_field}' was not found in the input layer. "
                    f"Available fields: {', '.join(source_fields)}"
                )

        # 2. Scan and aggregate features
        features = list(source.getFeatures())
        total_feats = len(features)
        if total_feats == 0:
            raise QgsProcessingException("The input layer contains no features.")

        # 3. Prepare output fields and sinks
        out_fields = QgsFields()
        out_fields.append(QgsField('h3_index', QVariant.String))
        out_fields.append(QgsField('base_total', QVariant.Int))
        out_fields.append(QgsField('ev_disaster', QVariant.Int))
        out_fields.append(QgsField('ev_total', QVariant.Int))
        out_fields.append(QgsField('ratio_event', QVariant.Double, len=10, prec=6))
        out_fields.append(QgsField('z_score', QVariant.Double, len=10, prec=4))
        out_fields.append(QgsField('p_value', QVariant.Double, len=10, prec=4))
        out_fields.append(QgsField('med_base', QVariant.Double, len=10, prec=2))
        out_fields.append(QgsField('alert', QVariant.Int))

        (sink_alert, dest_id_alert) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            out_fields,
            source.wkbType(),
            source.sourceCrs()
        )
        if sink_alert is None:
            raise QgsProcessingException("Failed to create the alert output feature sink.")

        (sink_event_all, dest_id_event_all) = self.parameterAsSink(
            parameters,
            self.OUTPUT_EVENT_ALL,
            context,
            source.fields(),
            source.wkbType(),
            source.sourceCrs()
        )
        (sink_event_disaster, dest_id_event_disaster) = self.parameterAsSink(
            parameters,
            self.OUTPUT_EVENT_DISASTER,
            context,
            source.fields(),
            source.wkbType(),
            source.sourceCrs()
        )

        feedback.setProgressText("Aggregating tweet records by H3 cell ID and date...")
        
        # h3_cells = { h3_id: {'geom': QgsGeometry, 'base_total': int, 'ev_disaster': int, 'ev_total': int} }
        h3_cells = {}
        
        for idx, feat in enumerate(features):
            if feedback.isCanceled():
                break
            
            # Get H3 Cell ID
            h3_val = feat[index_field]
            if h3_val is None or h3_val == NULL:
                continue
            h3_id = str(h3_val).strip()
            if not h3_id:
                continue
            
            # Parse date and extract year
            date_val = feat[date_field]
            year = extract_year(date_val)
            if year is None:
                continue
            
            # Skip data not belonging to our baseline or event years
            if year != baseline_year and year != event_year:
                continue
            
            # Initialize cell if encountered for the first time
            if h3_id not in h3_cells:
                h3_cells[h3_id] = {
                    'geom': QgsGeometry(feat.geometry()),  # Keep first geometry shape
                    'base_total': 0,
                    'ev_disaster': 0,
                    'ev_total': 0
                }
            
            # Increment count totals and populate raw event layers
            if year == baseline_year:
                h3_cells[h3_id]['base_total'] += 1
            elif year == event_year:
                h3_cells[h3_id]['ev_total'] += 1
                
                # Write to the All Event Tweets output layer
                if sink_event_all is not None:
                    sink_event_all.addFeature(feat, QgsFeatureSink.FastInsert)
                
                # Check classification label
                lbl_val = feat[label_field]
                is_dis = False
                if lbl_val is not None and lbl_val != NULL:
                    if str(lbl_val).strip() == disaster_label:
                        h3_cells[h3_id]['ev_disaster'] += 1
                        is_dis = True
                
                # Write to the Disaster Event Tweets output layer
                if is_dis and sink_event_disaster is not None:
                    sink_event_disaster.addFeature(feat, QgsFeatureSink.FastInsert)
            
            # Update progress bar (0% - 40%)
            if idx % 10000 == 0:
                feedback.setProgress(int((idx / total_feats) * 40))
        
        # 4. Validation \u0026 Stats
        n = len(h3_cells)
        if n == 0:
            raise QgsProcessingException(
                "No H3 cells were aggregated. Please verify that the index column, "
                "date formats, and year values are correct."
            )
        
        feedback.pushInfo(f"Successfully aggregated {n} unique H3 cells.")
        
        # Compute ratios and baseline count list
        ratios = {}
        baseline_counts = []
        for h3_id, data in h3_cells.items():
            base_t = float(data['base_total'])
            ev_d = float(data['ev_disaster'])
            ev_t = float(data['ev_total'])
            
            baseline_counts.append(base_t)
            if ev_t > 0.0:
                ratios[h3_id] = ev_d / ev_t
            else:
                ratios[h3_id] = 0.0
        
        # Calculate baseline median
        median_baseline = statistics.median(baseline_counts)
        feedback.pushInfo(f"Calculated baseline median tweet count: {median_baseline:.2f}")
        
        # Compute ratio global statistics for Getis-Ord Gi*
        ratio_values = list(ratios.values())
        mean_ratio = statistics.mean(ratio_values)
        std_ratio = statistics.pstdev(ratio_values)
        
        feedback.pushInfo(f"Global mean ratio: {mean_ratio:.4f}")
        feedback.pushInfo(f"Global standard deviation of ratio: {std_ratio:.4f}")
        
        if std_ratio == 0.0:
            feedback.pushWarning(
                "Warning: The standard deviation of the event ratio is 0. "
                "Getis-Ord Gi* z-scores will all be calculated as 0."
            )
        
        # 5. Build Spatial Index on aggregated unique H3 cell geometries
        feedback.setProgressText("Building spatial index for unique H3 cells...")
        
        # Create temp features to feed to spatial index
        index_features = []
        for idx, (h3_id, data) in enumerate(h3_cells.items()):
            t_feat = QgsFeature(idx)
            t_feat.setGeometry(data['geom'])
            index_features.append(t_feat)
        
        spatial_index = QgsSpatialIndex()
        for t_feat in index_features:
            spatial_index.addFeature(t_feat)
        
        # Bidirectional mapping
        id_to_h3 = {idx: h3_id for idx, (h3_id, _) in enumerate(h3_cells.items())}
        h3_to_id = {h3_id: idx for idx, h3_id in id_to_h3.items()}
        
        # Find Queen contiguity neighbors (including self)
        neighbors = {}
        for idx, t_feat in enumerate(index_features):
            h3_id = id_to_h3[idx]
            geom = t_feat.geometry()
            
            if geom.isEmpty():
                neighbors[h3_id] = [h3_id]
                continue
            
            candidates = spatial_index.intersects(geom.boundingBox())
            cell_neighbors = [h3_id]
            for cid in candidates:
                if cid == idx:
                    continue
                # Verify spatial intersection
                other_geom = index_features[cid].geometry()
                if geom.intersects(other_geom):
                    cell_neighbors.append(id_to_h3[cid])
            neighbors[h3_id] = cell_neighbors
        
        # 6. Compute Getis-Ord Gi* and evaluate alert criteria
        feedback.setProgressText("Running spatial hot spot analysis and exporting...")
        
        num_cells = float(n)
        alerts_generated = 0
        
        for idx, (h3_id, data) in enumerate(h3_cells.items()):
            if feedback.isCanceled():
                break
            
            ratio = ratios[h3_id]
            k_i = float(len(neighbors[h3_id]))  # Number of neighbors including self (w_ii = 1)
            
            z_score = 0.0
            p_value = 1.0
            
            if std_ratio > 0.0 and num_cells > 1.0:
                # Sum of ratios in the neighborhood
                sum_neighbors = sum(ratios[nid] for nid in neighbors[h3_id])
                
                # Getis-Ord Gi* formula parts
                numerator = sum_neighbors - (mean_ratio * k_i)
                denominator = std_ratio * math.sqrt(
                    (num_cells * k_i - k_i * k_i) / (num_cells - 1.0)
                )
                
                if denominator > 0.0:
                    z_score = numerator / denominator
                    # One-tailed upper tail p-value using the standard normal cumulative distribution function (CDF)
                    try:
                        p_value = 0.5 * (1.0 - math.erf(z_score / math.sqrt(2.0)))
                    except:
                        p_value = 1.0
            
            # Evaluate alert condition
            tweet_count = float(data['ev_total'])
            
            # Hotspot & baseline threshold filtering (filtering output features)
            if tweet_count > median_baseline and z_score >= z_threshold and p_value <= p_threshold:
                out_feat = QgsFeature()
                out_feat.setGeometry(data['geom'])
                out_feat.setFields(out_fields)
                
                out_feat.setAttribute('h3_index', h3_id)
                out_feat.setAttribute('base_total', data['base_total'])
                out_feat.setAttribute('ev_disaster', data['ev_disaster'])
                out_feat.setAttribute('ev_total', data['ev_total'])
                out_feat.setAttribute('ratio_event', ratio)
                out_feat.setAttribute('z_score', z_score)
                out_feat.setAttribute('p_value', p_value)
                out_feat.setAttribute('med_base', median_baseline)
                out_feat.setAttribute('alert', 1)
                
                sink_alert.addFeature(out_feat, QgsFeatureSink.FastInsert)
                alerts_generated += 1
            
            # Update progress bar (40% - 100%)
            feedback.setProgress(40 + int(((idx + 1) / num_cells) * 60))
        
        feedback.pushInfo(f"Successfully processed. Alerts generated: {alerts_generated}")
        
        # Return all output destination IDs so QGIS loads them automatically
        outputs = {self.OUTPUT: dest_id_alert}
        if dest_id_event_all is not None:
            outputs[self.OUTPUT_EVENT_ALL] = dest_id_event_all
        if dest_id_event_disaster is not None:
            outputs[self.OUTPUT_EVENT_DISASTER] = dest_id_event_disaster
        
        return outputs
